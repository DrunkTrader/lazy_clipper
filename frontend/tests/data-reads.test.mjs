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
  globalThis.window = { location: { search, pathname: '/' } }
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
