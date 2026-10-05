import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'
import { test } from 'node:test'
import { build } from 'vite'
import { projectResponse } from './fixtures.mjs'

// Use the existing Vite/TypeScript toolchain and Node's test runner. No DOM or
// additional test framework is needed for the request and polling boundaries.
const bundle = await build({
  configFile: false,
  logLevel: 'silent',
  define: { 'import.meta.env.VITE_API_BASE_URL': '""' },
  build: { write: false, minify: false, lib: { entry: 'tests/entry.ts', formats: ['es'] } },
})
const output = (Array.isArray(bundle) ? bundle[0] : bundle).output.find((item) => item.type === 'chunk')
const api = await import(`data:text/javascript;base64,${Buffer.from(output.code).toString('base64')}`)
const raw = 'candidate detection request failed 502 All routed attempts failed provider_error execution_id=private-id https://private.example api_key=private-key'

test('display uses local stage messages and never trusts raw or legacy error text', () => {
  for (const [stage, message] of Object.entries(api.stageMessages)) {
    assert.equal(api.publicError({ failed_stage: stage, error: { message: raw }, error_message: raw }), message)
  }
  assert.equal(api.publicError({ message: raw, error: raw, error_message: raw }), api.stageMessages.unknown)
  assert.equal(api.publicError({ failed_stage: '__proto__' }), api.stageMessages.unknown)
  assert.equal(api.publicError({ error: { code: 'toString', message: raw } }, 'rendering', true), api.stageMessages.rendering)
  assert.equal(api.errorMessage(new Error(raw)), api.stageMessages.unknown)
})

test('interrupted processing displays local retry guidance without trusting server text', () => {
  for (const stage of ['ingestion', 'transcription', 'analysis', 'rendering']) {
    const message = api.publicError({ failed_stage: stage, error: { code: 'PROCESSING_INTERRUPTED', message: raw } })
    assert.match(message, /interrupted/i)
    assert.match(message, /retry/i)
    assert.ok(!message.includes('private'))
  }
})

test('caption silence and missing timing use different safe messages', () => {
  const missing = api.publicError({ error: { code: 'CAPTION_ALIGNMENT_MISSING', message: raw } })
  const silent = api.publicError({ error: { code: 'CAPTION_NO_SPEECH', message: raw } })
  assert.match(missing, /transcript needs repair/)
  assert.match(silent, /no captionable speech/)
  assert.notEqual(missing, silent)
  assert.equal(api.publicError({ error: { code: '__proto__', message: raw } }), api.stageMessages.unknown)
})

test('capacity, duration limits, and timeouts use safe local guidance', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { code: 'PROCESSING_BUSY', message: raw } }), { status: 429 }))
  await assert.rejects(api.createProject('https://youtu.be/dQw4w9WgXcQ'), { message: 'Processing capacity is full. Please try again shortly.', status: 429 })
  for (const stage of ['ingestion', 'transcription', 'analysis', 'rendering']) {
    assert.match(api.publicError({ failed_stage: stage, error: { code: 'PROCESSING_TIMEOUT', message: raw } }), /exceeded its time limit/)
  }
  assert.match(api.publicError({ error: { code: 'SOURCE_DURATION_LIMIT', message: raw } }), /shorter video/)
  assert.match(api.publicError({ error: { code: 'CLIP_TOO_LONG', message: raw } }), /shorter range/)
})

test('all server errors and proxy HTML become safe endpoint-specific messages', async (t) => {
  for (const status of [500, 502, 503, 504]) {
    for (const body of [raw, JSON.stringify({ detail: raw }), JSON.stringify({ error: { code: 'INVALID_URL', message: raw } })]) {
      t.mock.method(globalThis, 'fetch', async () => new Response(body, { status }))
      await assert.rejects(api.createProject('https://youtu.be/dQw4w9WgXcQ'), { message: api.stageMessages.ingestion, status })
      await assert.rejects(api.getProjects(), { message: api.stageMessages.database, status })
      await assert.rejects(api.getClips('p'), { message: api.stageMessages.media, status })
      await assert.rejects(api.createClip('p', { moment_id: 'm', start: 0, end: 5 }), { message: api.stageMessages.rendering, status })
      t.mock.restoreAll()
    }
  }
})

test('only allowlisted client codes produce validation messages', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { code: 'INVALID_URL', message: raw } }), { status: 422 }))
  await assert.rejects(api.createProject('bad'), { message: 'Enter a valid YouTube video URL.' })
})

test('connection and body-read failures use the safe network message', async (t) => {
  for (const fail of [async () => { throw new Error(raw) }, async () => ({ text: async () => { throw new Error(raw) } })]) {
    t.mock.method(globalThis, 'fetch', fail)
    await assert.rejects(api.getProjects(), { message: 'Unable to reach the server. Please try again.', status: 0 })
    t.mock.restoreAll()
  }
})

test('a hung request is aborted and uses the network message', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  t.mock.method(globalThis, 'fetch', (_, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(new Error(raw)), { once: true })
  }))
  const failure = assert.rejects(api.getProjects(), { message: 'Unable to reach the server. Please try again.', status: 0 })
  t.mock.timers.tick(30000)
  await failure
})

for (const [processing, terminal] of [['ANALYZING', 'READY'], ['ANALYZING', 'FAILED'], ['RENDERING', 'READY']]) {
  test(`polls status only during ${processing}, refreshes once and stops at ${terminal}`, async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const calls = []
    const statuses = []
    let status = processing
    t.mock.method(globalThis, 'fetch', async (url) => {
      calls.push(url)
      return new Response(JSON.stringify(projectResponse(url, status)))
    })
    const stop = api.watchProject('p', {
      onStatus: (value) => statuses.push(value.status),
      onError: (error) => assert.fail(api.errorMessage(error)),
      onRefresh: async () => { await Promise.all([api.getProject('p'), api.getTranscript('p'), api.getMoments('p'), api.getClips('p')]) },
    })
    t.after(stop)
    await setImmediate()
    assert.equal(calls.length, 5)
    t.mock.timers.tick(4000)
    await setImmediate()
    assert.equal(calls.length, 6)
    assert.ok(calls[5].endsWith('/status'))
    status = terminal
    t.mock.timers.tick(4000)
    await setImmediate()
    assert.equal(calls.length, 11)
    t.mock.timers.tick(60000)
    await setImmediate()
    assert.equal(calls.length, 11)
    assert.deepEqual(statuses, [processing, processing, terminal])
  })
}

test('opening a terminal project loads details only once', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ project_id: 'p', status: 'READY' })))
  let refreshes = 0
  const stop = api.watchProject('p', { onStatus: () => {}, onError: assert.fail, onRefresh: async () => { refreshes++ } })
  t.after(stop)
  await setImmediate()
  t.mock.timers.tick(60000)
  await setImmediate()
  assert.equal(fetch.mock.callCount(), 1)
  assert.equal(refreshes, 1)
})

test('switching projects ignores a late response and cancels future polling', async (t) => {
  let finish
  t.mock.method(globalThis, 'fetch', () => new Promise((resolve) => { finish = resolve }))
  const stop = api.watchProject('old', { onStatus: assert.fail, onError: assert.fail, onRefresh: assert.fail })
  stop()
  finish(new Response(JSON.stringify({ project_id: 'old', status: 'READY' })))
  await setImmediate()
})
