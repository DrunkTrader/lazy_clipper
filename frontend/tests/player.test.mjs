import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'
import { test } from 'node:test'
import React from 'react'
import { act, create } from 'react-test-renderer'
import { projectResponse } from './fixtures.mjs'
import { bundleEntry } from './regressions.mjs'

const entry = await bundleEntry('tests/app-entry.ts')
let instance = 0

async function workspace(t, { apiAvailable = true, ready = true, search = '?project_id=p', fetchResponse } = {}) {
  const { App } = await import(`${entry}#instance=${instance++}`)
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const state = { time: 0, pauses: 0, playing: false, scripts: [], player: null, events: null }
  class Player {
    constructor(_host, options) {
      state.instances = (state.instances ?? 0) + 1
      state.events = options.events
      state.player = this
      if (ready) queueMicrotask(() => options.events.onReady?.({ target: this }))
    }
    seekTo(time) { state.time = time }
    playVideo() { state.playing = true }
    pauseVideo() { state.pauses++; state.playing = false }
    getCurrentTime() { return state.time }
    destroy() { state.destroyed = true }
  }
  state.Player = Player
  globalThis.window = {
    location: { search, pathname: '/', origin: 'http://test.local' },
    history: { replaceState() {} },
    YT: apiAvailable ? { Player } : undefined,
    setInterval: (...args) => globalThis.setInterval(...args),
    clearInterval: (...args) => globalThis.clearInterval(...args),
    setTimeout: (...args) => globalThis.setTimeout(...args),
    clearTimeout: (...args) => globalThis.clearTimeout(...args),
  }
  globalThis.document = {
    createElement: () => ({ remove() {} }),
    head: { appendChild: (script) => state.scripts.push(script) },
  }
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  t.mock.method(globalThis, 'fetch', fetchResponse ?? (async (url) => new Response(JSON.stringify(projectResponse(url)))))
  let tree
  t.after(async () => {
    if (tree) await act(async () => tree.unmount())
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
    if (originalDocument === undefined) delete globalThis.document
    else globalThis.document = originalDocument
  })
  await act(async () => {
    tree = create(React.createElement(App), { createNodeMock: () => ({ appendChild() {}, replaceChildren() {} }) })
    await setImmediate()
  })
  state.tree = tree
  state.select = async (index) => act(async () => {
    tree.root.findAll((node) => node.type === 'button' && node.children.join('').includes('Jump to moment'))[index].props.onClick()
  })
  state.tick = async (ms) => act(async () => { t.mock.timers.tick(ms); await setImmediate() })
  state.alerts = () => tree.root.findAllByProps({ role: 'alert' })
  return state
}

test('moment selection seeks, plays and pauses at its end', async (t) => {
  const state = await workspace(t)
  await state.select(0)
  assert.equal(state.playing, true)
  state.time = 20
  await state.tick(250)
  assert.equal(state.pauses, 1)
  assert.equal(state.playing, false)
})

test('F08 completed selection does not trap subsequent playback', async (t) => {
  const state = await workspace(t)
  await state.select(0)
  state.time = 20
  await state.tick(250)
  assert.equal(state.pauses, 1)
  state.player.playVideo()
  state.time = 25
  await state.tick(1000)
  assert.equal(state.pauses, 1)
  assert.equal(state.playing, true)
})

test('a new selection reinstates the playback boundary', async (t) => {
  const state = await workspace(t)
  await state.select(0)
  state.time = 20
  await state.tick(250)
  await state.select(1)
  assert.equal(state.time, 20)
  state.time = 30
  await state.tick(250)
  assert.equal(state.pauses, 1)
  assert.equal(state.playing, true)
  state.time = 40
  await state.tick(250)
  assert.equal(state.pauses, 2)
})

for (const [name, options] of [
  ['API script', { apiAvailable: false }],
  ['player readiness', { ready: false }],
]) {
  test(`F09 ${name} timeout produces a safe error`, async (t) => {
    const state = await workspace(t, options)
    await state.tick(30000)
    assert.ok(state.alerts().length > 0, 'A hung player must show an error within 30 seconds')
  })
}

test('script load failure renders a safe message', async (t) => {
  const state = await workspace(t, { apiAvailable: false })
  await act(async () => state.scripts[0].onerror())
  assert.equal(state.alerts().length, 1)
  assert.match(state.alerts()[0].children.join(''), /Internal server error while loading media/)
})

test('F09 load error is outside the clipped video frame', async (t) => {
  const state = await workspace(t, { apiAvailable: false })
  await act(async () => state.scripts[0].onerror())
  assert.equal(state.alerts().length, 1)
  for (let node = state.alerts()[0].parent; node; node = node.parent) {
    assert.notEqual(node.props.className, 'youtube-player-frame', 'Error text must not follow the full-height host inside its clipped frame')
  }
})

test('F09 unavailable/embedding-disabled player reports an error', async (t) => {
  const state = await workspace(t)
  assert.equal(typeof state.events.onError, 'function')
  await act(async () => state.events.onError({ data: 101 }))
  assert.equal(state.alerts().length, 1)
})

test('player retry creates a fresh player and ignores stale readiness callbacks', async (t) => {
  const state = await workspace(t, { ready: false })
  const oldEvents = state.events
  await state.tick(15000)
  assert.equal(state.alerts().length, 1)
  await act(async () => oldEvents.onReady())
  assert.equal(state.alerts().length, 1)
  const retry = state.tree.root.findAllByType('button').find((node) => node.children.join('') === 'Retry player')
  await act(async () => { retry.props.onClick(); await setImmediate() })
  assert.equal(state.instances, 2)
  assert.equal(state.alerts().length, 0)
  await act(async () => state.events.onReady())
  await state.select(0)
  assert.equal(state.playing, true)
  await state.tick(15000)
  assert.equal(state.alerts().length, 0)
})

test('script failure can retry after the API becomes available', async (t) => {
  const state = await workspace(t, { apiAvailable: false })
  await act(async () => state.scripts[0].onerror())
  const link = state.tree.root.findAllByType('a').find((node) => node.children.join('') === 'Open on YouTube')
  assert.equal(link.props.href, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
  window.YT = { Player: state.Player }
  const retry = state.tree.root.findAllByType('button').find((node) => node.children.join('') === 'Retry player')
  await act(async () => { retry.props.onClick(); await setImmediate() })
  assert.equal(state.instances, 1)
  assert.equal(state.alerts().length, 0)
})

test('newly available video metadata mounts the player after client-side project creation', async (t) => {
  let processingStatus = 'QUEUED'
  let newProjectReads = 0
  const requests = []
  const otherProject = { ...projectResponse('/api/v1/projects/p'), id: 'other', title: 'Other video' }
  const fetchResponse = async (url, init = {}) => {
    const path = new URL(url, 'http://test.local').pathname
    requests.push(`${init.method ?? 'GET'} ${path}`)
    if (init.method === 'POST') return new Response(JSON.stringify({ project_id: 'new', status: 'queued' }), { status: 202 })
    if (path.endsWith('/projects') && !path.endsWith('/projects/new')) return new Response(JSON.stringify([otherProject]))
    if (path.endsWith('/status')) {
      const id = path.split('/').at(-2)
      return new Response(JSON.stringify({ project_id: id, status: id === 'new' ? processingStatus : 'READY', message: null }))
    }
    if (path.endsWith('/projects/new')) {
      const includeVideo = newProjectReads++ > 0
      return new Response(JSON.stringify({ ...otherProject, id: 'new', title: 'New video', status: processingStatus, video: includeVideo ? otherProject.video : null }))
    }
    if (path.endsWith('/projects/other')) return new Response(JSON.stringify(otherProject))
    if (path.endsWith('/transcript')) return new Response(JSON.stringify({ project_id: path.split('/').at(-2), segments: [] }))
    if (path.endsWith('/moments')) return new Response(JSON.stringify({ project_id: path.split('/').at(-2), moments: [] }))
    if (path.endsWith('/clips')) return new Response(JSON.stringify({ project_id: path.split('/').at(-2), clips: [] }))
    throw new Error(`Unexpected fixture request: ${path}`)
  }
  const state = await workspace(t, { search: '', fetchResponse })
  const form = state.tree.root.findByType('form')
  const input = state.tree.root.findByType('input')
  await act(async () => input.props.onChange({ target: { value: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' } }))
  await act(async () => {
    form.props.onSubmit({ preventDefault() {} })
    await setImmediate()
    await setImmediate()
  })
  assert.equal(state.instances, undefined)

  processingStatus = 'INGESTING'
  await state.tick(4000)
  assert.equal(state.instances, 1, requests.join(', '))
  assert.equal(state.player.destroyed, undefined)

  const otherLink = state.tree.root.findAllByType('a').find((node) => node.children.join('') === 'Other video')
  await act(async () => {
    otherLink.props.onClick({ preventDefault() {} })
    await setImmediate()
  })
  assert.equal(state.instances, 2)
  assert.equal(state.destroyed, true)
})

test('invalid project URLs stay on the form without mounting a player', async (t) => {
  let posts = 0
  const state = await workspace(t, {
    search: '',
    fetchResponse: async (_url, init = {}) => {
      if (init.method === 'POST') posts++
      return new Response('[]')
    },
  })
  const form = state.tree.root.findByType('form')
  const input = state.tree.root.findByType('input')
  await act(async () => input.props.onChange({ target: { value: 'not a YouTube URL' } }))
  await act(async () => form.props.onSubmit({ preventDefault() {} }))
  assert.equal(posts, 0)
  assert.equal(state.instances, undefined)
  assert.equal(state.alerts().length, 1)
})
