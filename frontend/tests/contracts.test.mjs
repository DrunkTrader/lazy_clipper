import assert from 'node:assert/strict'
import { test } from 'node:test'
import { bundleEntry } from './regressions.mjs'
import { clip, moments, project } from './fixtures.mjs'

const api = await import(await bundleEntry('tests/entry.ts'))
const cases = [
  ['projects object', () => api.getProjects(), {}, 'database'],
  ['projects null', () => api.getProjects(), null, 'database'],
  ['projects null element', () => api.getProjects(), [null], 'database'],
  ['projects missing identity', () => api.getProjects(), [{}], 'database'],
  ['project object', () => api.getProject('p'), {}, 'database'],
  ['project null', () => api.getProject('p'), null, 'database'],
  ['project invalid status', () => api.getProject('p'), { ...project, status: {} }, 'database'],
  ['status object', () => api.getProjectStatus('p'), {}, 'database'],
  ['clips object', () => api.getClips('p'), {}, 'media'],
  ['clips null', () => api.getClips('p'), null, 'media'],
  ['clips null element', () => api.getClips('p'), { project_id: 'p', clips: [null] }, 'media'],
  ['clips malformed element', () => api.getClips('p'), { project_id: 'p', clips: [{}] }, 'media'],
  ['clip invalid range', () => api.createClip('p', clip), { ...clip, start: 'bad' }, 'rendering'],
  ['clip invalid status', () => api.createClip('p', clip), { ...clip, status: {} }, 'rendering'],
  ['transcript malformed segment', () => api.getTranscript('p'), { project_id: 'p', segments: [null] }, 'database'],
  ['moments malformed element', () => api.getMoments('p'), { project_id: 'p', moments: [{}] }, 'database'],
  ['ingest missing identity', () => api.createProject(project.source_url), {}, 'ingestion'],
  ['project unknown status', () => api.getProject('p'), { ...project, status: 'FUTURE_STATE' }, 'database'],
  ['project invalid video', () => api.getProject('p'), { ...project, video: [] }, 'database'],
  ['project wrong identity', () => api.getProject('p'), { ...project, id: 'other' }, 'database'],
  ['status wrong identity', () => api.getProjectStatus('p'), { project_id: 'other', status: 'READY' }, 'database'],
  ['project invalid failure', () => api.getProject('p'), { ...project, error: [] }, 'database'],
  ['clip reversed range', () => api.createClip('p', clip), { ...clip, start: 20, end: 10 }, 'rendering'],
  ['clip invalid media URL', () => api.createClip('p', clip), { ...clip, media_url: 'javascript:alert(1)' }, 'rendering'],
  ['clip wrong moment', () => api.createClip('p', clip), { ...clip, moment_id: 'other' }, 'rendering'],
  ['clips cross-project element', () => api.getClips('p'), { project_id: 'p', clips: [{ ...clip, project_id: 'other' }] }, 'media'],
  ['transcript wrong identity', () => api.getTranscript('p'), { project_id: 'other', segments: [] }, 'database'],
  ['transcript invalid timing', () => api.getTranscript('p'), { project_id: 'p', segments: [{ id: 's', start: '0', end: 1, text: 'Saved' }] }, 'database'],
  ['moments invalid title', () => api.getMoments('p'), { project_id: 'p', moments: [{ ...moments[0], title: {} }] }, 'database'],
]

for (const [name, request, body, stage] of cases) {
  test(`F07 rejects HTTP 200 ${name}`, async (t) => {
    t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(body)))
    await assert.rejects(request(), { name: 'ApiError', status: 500, message: api.stageMessages[stage] })
  })
}

test('valid empty lists, project and queued clip remain successful', async (t) => {
  for (const [request, body] of [
    [() => api.getProjects(), []],
    [() => api.getProject('p'), project],
    [() => api.getClips('p'), { project_id: 'p', clips: [] }],
    [() => api.createClip('p', clip), clip],
    [() => api.createProject(project.source_url), { project_id: 'p', status: 'queued' }],
    [() => api.getProjectStatus('p'), { project_id: 'p', status: 'FAILED', message: null, error: { code: 'PROCESSING_ERROR', message: 'Safe' } }],
    [() => api.getTranscript('p'), { project_id: 'p', segments: [{ id: 's', start: 0, end: 1, text: 'Saved', speaker: null, words: [] }] }],
    [() => api.getMoments('p'), { project_id: 'p', moments }],
    [() => api.getClips('p'), { project_id: 'p', clips: [{ ...clip, status: 'READY', media_url: '/api/clip', download_url: 'https://clips.example.test/api/clip?download=true' }] }],
  ]) {
    t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(body)))
    assert.deepEqual(await request(), body)
    t.mock.restoreAll()
  }
})

test('relative clip media paths resolve against the configured API origin', async () => {
  assert.equal(api.resolveApiMediaUrl('/api/v1/projects/p/clips/c/media'), '/api/v1/projects/p/clips/c/media')
  assert.equal(api.resolveApiMediaUrl('https://cdn.example.test/clip.mp4'), 'https://cdn.example.test/clip.mp4')
})
