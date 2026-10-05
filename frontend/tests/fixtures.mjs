export const project = {
  id: 'p', title: 'Saved video', status: 'READY',
  source_url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
  created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
  status_message: null, error_message: null,
  video: { id: 'v', youtube_id: 'dQw4w9WgXcQ', duration: 60 },
}

export const moments = [0, 1].map((rank) => ({
  id: `m${rank}`, title: `Moment ${rank}`, description: 'Saved idea', reason: 'Payoff',
  start: rank * 20, end: (rank + 1) * 20, score: 8, rank,
}))

export const clip = {
  id: 'c', project_id: 'p', moment_id: 'm0', start: 0, end: 20,
  status: 'QUEUED', error_message: null, media_url: null, download_url: null,
}

export function projectResponse(url, status = 'READY') {
  const path = new URL(url, 'http://test.local').pathname
  if (path.endsWith('/status')) return { project_id: 'p', status, message: null, error: null }
  if (path.endsWith('/transcript')) return { project_id: 'p', segments: [] }
  if (path.endsWith('/moments')) return { project_id: 'p', moments }
  if (path.endsWith('/clips')) return { project_id: 'p', clips: [] }
  if (path.endsWith('/projects')) return [project]
  if (path.endsWith('/projects/p')) return { ...project, status }
  throw new Error(`Unexpected fixture request: ${path}`)
}
