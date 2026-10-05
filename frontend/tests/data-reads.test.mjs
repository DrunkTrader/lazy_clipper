import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'
import { test } from 'node:test'
import React from 'react'
import { act, create } from 'react-test-renderer'
import { bundleEntry } from './regressions.mjs'
import { clip, project, projectResponse } from './fixtures.mjs'

const { App } = await import(await bundleEntry('tests/app-entry.ts'))

async function mount(t, search, fetch) {
  const original = globalThis.window
  globalThis.window = {
    location: { search, pathname: '/' },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  }
  t.mock.method(globalThis, 'fetch', fetch)
  let tree
  t.after(async () => {
    if (tree) await act(async () => tree.unmount())
    if (original === undefined) delete globalThis.window
    else globalThis.window = original
  })
  await act(async () => { tree = create(React.createElement(App)); await setImmediate() })
  return tree
}

test('clip submission/completion refreshes mutable details without rereading saved transcript/moments', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let status = 'READY', hasClip = false
  const reads = []
  const tree = await mount(t, '?project_id=p', async (url, init) => {
    const path = new URL(url, 'http://test.local').pathname
    if (init.method === 'POST') {
      hasClip = true
      status = 'RENDERING'
      return new Response(JSON.stringify(clip), { status: 202 })
    }
    reads.push(url)
    const body = projectResponse(url, status)
    if (path.endsWith('/projects/p')) body.video = null
    if (path.endsWith('/clips') && hasClip) body.clips = [{ ...clip, status: status === 'READY' ? 'READY' : 'RENDERING' }]
    return new Response(JSON.stringify(body))
  })
  const contentReads = () => reads.filter((url) => /\/(transcript|moments)(\?|$)/.test(url))
  assert.equal(contentReads().length, 2)
  const createClip = tree.root.findAllByType('button').find((node) => node.children.join('') === 'Create Clip')
  await act(async () => { createClip.props.onClick(); await setImmediate() })
  assert.equal(contentReads().length, 2)
  status = 'READY'
  await act(async () => { t.mock.timers.tick(4000); await setImmediate() })
  assert.equal(contentReads().length, 2)
  assert.ok(reads.filter((url) => url.endsWith('/clips')).length >= 3)
  assert.ok(contentReads().find((url) => url.includes('/transcript?include_words=false')))
})

test('clip range controls send an adjusted range to the existing clip endpoint', async (t) => {
  let payload
  const tree = await mount(t, '?project_id=p', async (url, init = {}) => {
    const path = new URL(url, 'http://test.local').pathname
    if (init.method === 'POST') {
      payload = JSON.parse(init.body)
      return new Response(JSON.stringify({ ...clip, start: payload.start, end: payload.end }), { status: 202 })
    }
    return new Response(JSON.stringify(projectResponse(url)))
  })
  const inputs = tree.root.findAllByType('input')
  const start = inputs.find((node) => node.props['aria-label'] === 'Clip start for Moment 0')
  const end = inputs.find((node) => node.props['aria-label'] === 'Clip end for Moment 0')
  start.props.onChange({ target: { value: '3.5' } })
  end.props.onChange({ target: { value: '12.5' } })
  const button = tree.root.findAllByType('button').find((node) => node.children.join('') === 'Create Clip')
  await act(async () => { button.props.onClick(); await setImmediate() })
  assert.deepEqual(payload, { moment_id: 'm0', start: 3.5, end: 12.5 })
})

test('ingestion completion still reloads newly produced transcript/moments', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let status = 'ANALYZING'
  const reads = []
  await mount(t, '?project_id=p', async (url) => {
    reads.push(url)
    const body = projectResponse(url, status)
    if (new URL(url, 'http://test.local').pathname.endsWith('/projects/p')) body.video = null
    return new Response(JSON.stringify(body))
  })
  status = 'READY'
  await act(async () => { t.mock.timers.tick(4000); await setImmediate() })
  assert.equal(reads.filter((url) => /\/(transcript|moments)(\?|$)/.test(url)).length, 4)
})

test('saved-project pagination preserves the current page when loading more fails', async (t) => {
  const projects = Array.from({ length: 31 }, (_, index) => ({ ...project, id: `p${index}`, title: `Project ${index}` }))
  let fail = true
  const tree = await mount(t, '', async (url) => {
    const params = new URL(url, 'http://test.local').searchParams
    const offset = Number(params.get('offset') ?? 0)
    assert.equal(Number(params.get('limit')), 25)
    if (offset && fail) { fail = false; return new Response('{}', { status: 503 }) }
    return new Response(JSON.stringify(projects.slice(offset, offset + 25)))
  })
  const saved = () => tree.root.findByProps({ className: 'saved-project-list' }).findAllByType('li')
  assert.equal(saved().length, 25)
  const more = () => tree.root.findAllByType('button').find((node) => node.children.join('') === 'Load more projects')
  assert.ok(more())
  await act(async () => { more().props.onClick(); await setImmediate() })
  assert.equal(saved().length, 25)
  assert.ok(tree.root.findAllByProps({ role: 'alert' }).length)
  await act(async () => { more().props.onClick(); await setImmediate() })
  assert.equal(saved().length, 31)
  assert.equal(more(), undefined)
})

test('deleting the open project confirms, sends DELETE, and clears local workspace state', async (t) => {
  const originalWindow = globalThis.window
  let deleted = false
  let replacedPath = null
  globalThis.window = {
    location: { search: '?project_id=p', pathname: '/' },
    history: { replaceState: (_, __, path) => { replacedPath = path } },
    confirm: () => true,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  }
  t.mock.method(globalThis, 'fetch', async (url, init = {}) => {
    const path = new URL(url, 'http://test.local').pathname
    if (init.method === 'DELETE') {
      deleted = true
      return new Response(null, { status: 204 })
    }
    if (path.endsWith('/projects') && deleted) return new Response('[]')
    return new Response(JSON.stringify(projectResponse(url)))
  })
  let tree
  t.after(async () => {
    if (tree) await act(async () => tree.unmount())
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
  })
  await act(async () => { tree = create(React.createElement(App)); await setImmediate() })
  const button = tree.root.findByProps({ 'aria-label': 'Delete project' })
  await act(async () => { button.props.onClick(); await setImmediate() })
  assert.equal(deleted, true)
  assert.equal(replacedPath, '/')
  assert.equal(tree.root.findAllByProps({ className: 'saved-project-list' })[0].findAllByType('li').length, 0)
  assert.equal(tree.root.findAllByProps({ className: 'workspace' }).length, 0)
})

test('cancelling project deletion does not send a request', async (t) => {
  const originalWindow = globalThis.window
  let deletes = 0
  globalThis.window = {
    location: { search: '?project_id=p', pathname: '/' },
    history: { replaceState() {} },
    confirm: () => false,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  }
  t.mock.method(globalThis, 'fetch', async (url, init = {}) => {
    if (init.method === 'DELETE') deletes += 1
    return new Response(JSON.stringify(projectResponse(url)))
  })
  let tree
  t.after(async () => {
    if (tree) await act(async () => tree.unmount())
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
  })
  await act(async () => { tree = create(React.createElement(App)); await setImmediate() })
  await act(async () => { tree.root.findByProps({ 'aria-label': 'Delete project' }).props.onClick(); await setImmediate() })
  assert.equal(deletes, 0)
})
