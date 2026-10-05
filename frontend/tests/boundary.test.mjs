import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'
import { test } from 'node:test'
import React from 'react'
import { act, create } from 'react-test-renderer'
import { bundleEntry } from './regressions.mjs'
import { projectResponse } from './fixtures.mjs'

const { App, ErrorBoundary } = await import(await bundleEntry('tests/app-entry.ts'))

for (const suffix of ['/projects', '/clips']) {
  test(`malformed ${suffix} response keeps the React workspace usable`, async (t) => {
    const original = globalThis.window
    globalThis.window = { location: { search: '?project_id=p', pathname: '/' } }
    const requests = []
    t.mock.method(globalThis, 'fetch', async (url, init) => {
      requests.push(init.method ?? 'GET')
      const path = new URL(url, 'http://test.local').pathname
      const body = path.endsWith(suffix) ? { private: 'provider diagnostic' } : projectResponse(url)
      if (path.endsWith('/projects/p')) body.video = null
      return new Response(JSON.stringify(body))
    })
    let tree
    t.after(async () => {
      if (tree) await act(async () => tree.unmount())
      if (original === undefined) delete globalThis.window
      else globalThis.window = original
    })
    await act(async () => {
      tree = create(React.createElement(ErrorBoundary, null, React.createElement(App)))
      await setImmediate()
    })
    assert.equal(tree.root.findAllByType('form').length, 1)
    assert.equal(tree.root.findAllByType('main').length, 1)
    assert.ok(tree.root.findAllByProps({ role: 'alert' }).length > 0)
    const rendered = JSON.stringify(tree.toJSON())
    assert.ok(rendered.includes('Saved video'))
    assert.ok(!rendered.includes('provider diagnostic'))
    assert.ok(!rendered.includes('workspace could not be displayed'))
    assert.ok(requests.every((method) => method === 'GET'))
  })
}

test('render boundary shows local recovery text and reloads without submitting jobs', async (t) => {
  const original = globalThis.window
  let reloads = 0
  globalThis.window = { location: { reload() { reloads++ } } }
  t.mock.method(console, 'error', () => {}) // Expected React development diagnostic.
  t.mock.method(globalThis, 'fetch', () => assert.fail('The fallback must not submit work'))
  function Broken() { throw new Error('private provider body') }
  let tree
  t.after(async () => {
    if (tree) await act(async () => tree.unmount())
    if (original === undefined) delete globalThis.window
    else globalThis.window = original
  })
  await act(async () => { tree = create(React.createElement(ErrorBoundary, null, React.createElement(Broken))) })
  assert.equal(tree.root.findAllByProps({ role: 'alert' }).length, 1)
  const rendered = JSON.stringify(tree.toJSON())
  assert.match(rendered, /workspace could not be displayed/)
  assert.ok(!rendered.includes('private provider body'))
  await act(async () => tree.root.findByType('button').props.onClick())
  assert.equal(reloads, 1)
})
