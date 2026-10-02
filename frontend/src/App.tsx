import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  JsonObject,
  Moment,
  Project,
  ProjectStatus,
  TranscriptSegment,
  asRecord,
  createProject,
  extractItems,
  extractProjectId,
  formatTime,
  getNumber,
  getProject,
  getProjectStatus,
  getMoments,
  getString,
  getTranscript,
} from './api'
import './styles.css'

type Selection = { start: number; end?: number; label: string } | null

function readTime(item: JsonObject, names: string[]): number | undefined {
  for (const name of names) {
    const value = getNumber(item[name])
    if (value !== undefined) return value
  }
  return undefined
}

function normalizeTranscript(value: unknown): TranscriptSegment[] {
  return extractItems(value, ['segments', 'transcript', 'items']).map((item) => ({
    ...item,
    start: readTime(item, ['start', 'start_time', 'timestamp']),
    end: readTime(item, ['end', 'end_time']),
    text: getString(item.text) ?? getString(item.content) ?? '',
    speaker: getString(item.speaker),
  }))
}

function normalizeMoments(value: unknown): Moment[] {
  return extractItems(value, ['moments', 'items']).map((item) => ({
    ...item,
    start: readTime(item, ['start', 'start_time']),
    end: readTime(item, ['end', 'end_time']),
    title: getString(item.title) ?? 'Untitled moment',
    description: getString(item.description),
    reason: getString(item.reason),
    score: getNumber(item.score),
  }))
}

function statusName(status: ProjectStatus | null): string {
  return (getString(status?.status) ?? 'UNKNOWN').toUpperCase()
}

function isReady(status: string): boolean {
  return ['READY', 'COMPLETED', 'DONE'].includes(status)
}

function isFailed(status: string): boolean {
  return ['FAILED', 'ERROR', 'CANCELLED'].includes(status)
}

function getMediaUrl(project: Project | null): string | undefined {
  if (!project) return undefined
  const video = asRecord(project.video)
  return (
    getString(project.video_url) ??
    getString(project.media_url) ??
    getString(project.playback_url) ??
    getString(project.video_path_url) ??
    getString(video.url) ??
    getString(video.video_url) ??
    getString(video.media_url)
  )
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'The request failed.'
}

function App() {
  const [url, setUrl] = useState('')
  const [projectId, setProjectId] = useState(() => new URLSearchParams(window.location.search).get('project_id') ?? '')
  const [project, setProject] = useState<Project | null>(null)
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])
  const [moments, setMoments] = useState<Moment[]>([])
  const [createError, setCreateError] = useState<string | null>(null)
  const [projectError, setProjectError] = useState<string | null>(null)
  const [contentError, setContentError] = useState<string | null>(null)
  const [projectLoading, setProjectLoading] = useState(false)
  const [contentLoading, setContentLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState<Selection>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    let firstLoad = true

    const loadProject = async () => {
      if (firstLoad) setProjectLoading(true)
      try {
        const [nextProject, nextStatus] = await Promise.all([getProject(projectId), getProjectStatus(projectId)])
        if (cancelled) return
        setProject(nextProject)
        setStatus(nextStatus)
        setProjectError(null)
      } catch (error) {
        if (!cancelled) setProjectError(errorMessage(error))
      } finally {
        if (!cancelled) {
          firstLoad = false
          setProjectLoading(false)
        }
      }
    }

    void loadProject()
    const interval = window.setInterval(() => void loadProject(), 4000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [projectId])

  useEffect(() => {
    const currentStatus = statusName(status)
    if (!projectId || !isReady(currentStatus)) return
    let cancelled = false
    setContentLoading(true)
    setContentError(null)

    Promise.all([getTranscript(projectId), getMoments(projectId)])
      .then(([transcriptResponse, momentsResponse]) => {
        if (cancelled) return
        setTranscript(normalizeTranscript(transcriptResponse))
        setMoments(normalizeMoments(momentsResponse))
      })
      .catch((error) => {
        if (!cancelled) setContentError(errorMessage(error))
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [projectId, statusName(status)])

  const currentStatus = statusName(status)
  const failed = isFailed(currentStatus)
  const mediaUrl = getMediaUrl(project)
  const title = getString(project?.title) ?? getString(asRecord(project?.video).title) ?? 'Untitled project'
  const sourceUrl = getString(project?.source_url) ?? getString(project?.url)
  const statusMessage = failed
    ? (getString(status?.error) ?? getString(status?.message))
    : (getString(status?.message) ?? getString(status?.error))

  const sortedTranscript = useMemo(
    () => [...transcript].sort((a, b) => (a.start ?? 0) - (b.start ?? 0)),
    [transcript],
  )

  function seekTo(start: number | undefined, end: number | undefined, label: string) {
    if (start === undefined) return
    setSelected({ start, end, label })
    const video = videoRef.current
    if (video) {
      video.currentTime = start
      void video.play().catch(() => undefined)
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedUrl = url.trim()
    if (!trimmedUrl) {
      setCreateError('Enter a video URL to create a project.')
      return
    }
    try {
      new URL(trimmedUrl)
    } catch {
      setCreateError('Enter a valid URL, including https://.')
      return
    }

    setCreating(true)
    setCreateError(null)
    setProjectError(null)
    setContentError(null)
    try {
      const response = await createProject(trimmedUrl)
      const newProjectId = extractProjectId(response)
      if (!newProjectId) throw new Error('The API did not return a project_id.')
      setProject(null)
      setStatus(null)
      setTranscript([])
      setMoments([])
      setSelected(null)
      setProjectId(newProjectId)
      window.history.replaceState({}, '', `${window.location.pathname}?project_id=${encodeURIComponent(newProjectId)}`)
    } catch (error) {
      setCreateError(errorMessage(error))
    } finally {
      setCreating(false)
    }
  }

  return (
    <main className="app-shell">
      <header className="site-header">
        <div>
          <p className="eyebrow">VIDEO WORKSPACE</p>
          <h1>Lazy Clipper</h1>
          <p className="subtitle">Find the moments worth keeping.</p>
        </div>
        <div className="api-indicator"><span className="status-dot" /> Live API</div>
      </header>

      <section className="create-panel panel">
        <div>
          <p className="eyebrow">NEW PROJECT</p>
          <h2>Start with a video URL</h2>
          <p className="muted">The API will ingest the video, transcribe it, and identify moments.</p>
        </div>
        <form className="create-form" onSubmit={handleCreate}>
          <label htmlFor="video-url">Video URL</label>
          <div className="form-row">
            <input
              id="video-url"
              type="url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
              disabled={creating}
            />
            <button type="submit" disabled={creating}>{creating ? 'Creating…' : 'Create project'}</button>
          </div>
          {createError && <p className="error-text" role="alert">{createError}</p>}
        </form>
      </section>

      {projectId && (
        <section className="workspace">
          {projectLoading && !project && <div className="panel loading-state">Loading project…</div>}
          {projectError && <div className="alert error-box" role="alert"><strong>Could not load project.</strong> {projectError}</div>}

          {project && (
            <>
              <section className="project-heading panel">
                <div>
                  <p className="eyebrow">PROJECT</p>
                  <h2>{title}</h2>
                  <p className="project-id">ID: {getString(project.project_id) ?? getString(project.id) ?? projectId}</p>
                </div>
                <div className={`status-pill ${failed ? 'failed' : isReady(currentStatus) ? 'ready' : ''}`}>
                  <span className="status-dot" /> {currentStatus}
                </div>
              </section>

              {failed && (
                <div className="alert failed-box" role="alert">
                  <strong>Project processing failed.</strong>
                  <span>{statusMessage ?? 'The API reported a FAILED state. No generated data is available.'}</span>
                </div>
              )}
              {!failed && !isReady(currentStatus) && (
                <div className="alert progress-box">
                  <strong>Processing: {currentStatus}</strong>
                  <span>{statusMessage ?? 'The project is still being processed. This page checks for updates automatically.'}</span>
                </div>
              )}

              <section className="metadata panel">
                <div className="section-heading"><div><p className="eyebrow">DETAILS</p><h2>Project metadata</h2></div></div>
                <div className="metadata-grid">
                  <Metadata label="Title" value={title} />
                  <Metadata label="Source" value={sourceUrl} link={sourceUrl} />
                  <Metadata label="Created" value={formatDate(project.created_at)} />
                  <Metadata label="Updated" value={formatDate(project.updated_at)} />
                </div>
              </section>

              <section className="video-section panel">
                <div className="section-heading"><div><p className="eyebrow">SOURCE VIDEO</p><h2>Preview</h2></div>{selected && <span className="selection-label">Selected: {selected.label}</span>}</div>
                {mediaUrl ? (
                  <video ref={videoRef} className="video-player" controls preload="metadata" src={mediaUrl} />
                ) : (
                  <div className="empty-state video-unavailable">
                    <strong>No playable media URL was returned by the API.</strong>
                    <span>Metadata, transcript, and moments are still shown below. The player will appear when the backend provides a video URL.</span>
                  </div>
                )}
              </section>

              <div className="content-grid">
                <section className="moments-section panel">
                  <div className="section-heading"><div><p className="eyebrow">AI SUGGESTIONS</p><h2>Suggested moments</h2></div><span className="count">{moments.length}</span></div>
                  {contentLoading && <p className="muted">Loading moments…</p>}
                  {!contentLoading && contentError && <p className="error-text" role="alert">Could not load moments: {contentError}</p>}
                  {!contentLoading && !contentError && moments.length === 0 && <Empty text="No moments generated yet." />}
                  <div className="moment-list">
                    {moments.map((moment, index) => {
                      const start = moment.start
                      const end = moment.end
                      const key = getString(moment.id) ?? `${start ?? index}-${index}`
                      return <article className={`moment-card ${selected?.label === (moment.title ?? '') ? 'selected' : ''}`} key={key}>
                        <div className="moment-topline"><span className="moment-number">{String(index + 1).padStart(2, '0')}</span><span className="time-range">{formatTime(start)} – {formatTime(end)}</span></div>
                        <h3>{moment.title}</h3>
                        {moment.description && <p>{moment.description}</p>}
                        {moment.reason && <p className="reason"><strong>Why it stands out:</strong> {moment.reason}</p>}
                        <div className="moment-footer">
                          {moment.score !== undefined && <span className="score">Score {moment.score}</span>}
                          <button className="text-button" onClick={() => seekTo(start, end, moment.title ?? 'Moment')} disabled={start === undefined}>Seek video →</button>
                        </div>
                      </article>
                    })}
                  </div>
                </section>

                <section className="transcript-section panel">
                  <div className="section-heading"><div><p className="eyebrow">TRANSCRIPT</p><h2>Transcript</h2></div><span className="count">{transcript.length}</span></div>
                  {contentLoading && <p className="muted">Loading transcript…</p>}
                  {!contentLoading && contentError && <p className="error-text" role="alert">Could not load transcript: {contentError}</p>}
                  {!contentLoading && !contentError && sortedTranscript.length === 0 && <Empty text="No transcript segments available yet." />}
                  <div className="transcript-list">
                    {sortedTranscript.map((segment, index) => {
                      const start = segment.start
                      const end = segment.end
                      return <button className="transcript-row" key={getString(segment.id) ?? `${start ?? index}-${index}`} onClick={() => seekTo(start, end, `Transcript at ${formatTime(start)}`)} disabled={start === undefined}>
                        <span className="timestamp">{formatTime(start)}</span>
                        <span className="transcript-copy">{segment.speaker && <strong>{segment.speaker}: </strong>}{segment.text || '—'}</span>
                      </button>
                    })}
                  </div>
                </section>
              </div>
            </>
          )}
        </section>
      )}

      {!projectId && <div className="welcome-empty panel"><p className="eyebrow">READY WHEN YOU ARE</p><h2>Create a project to inspect its video.</h2><p className="muted">Nothing is shown until the API returns a real project.</p></div>}
    </main>
  )
}

function Metadata({ label, value, link }: { label: string; value?: string; link?: string }) {
  return <div className="metadata-item"><span>{label}</span>{link ? <a href={link} target="_blank" rel="noreferrer">{value}</a> : <strong>{value ?? '—'}</strong>}</div>
}

function Empty({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>
}

function formatDate(value: unknown): string | undefined {
  const date = getString(value)
  if (!date) return undefined
  const parsed = new Date(date)
  return Number.isNaN(parsed.getTime()) ? date : parsed.toLocaleString()
}

export default App
