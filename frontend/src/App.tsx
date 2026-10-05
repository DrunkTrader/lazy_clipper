import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  GeneratedClip,
  JsonObject,
  Moment,
  Project,
  ProjectStatus,
  PROJECTS_PAGE_SIZE,
  TranscriptSegment,
  asRecord,
  createClip,
  createProject,
  extractItems,
  extractProjectId,
  formatTime,
  getClips,
  getNumber,
  getProject,
  getProjects,
  getMoments,
  getString,
  getTranscript,
} from './api'
import { errorMessage, publicError, stageMessages } from './errors'
import { watchProject } from './projectUpdates'
import './styles.css'

type Selection = { id?: string; start: number; end?: number; label: string } | null

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
  const name = (getString(status?.status) ?? 'UNKNOWN').toUpperCase()
  return ['QUEUED', 'INGESTING', 'TRANSCRIBING', 'ANALYZING', 'RENDERING', 'READY', 'COMPLETED', 'DONE', 'FAILED', 'ERROR', 'CANCELLED'].includes(name) ? name : 'UNKNOWN'
}

function isReady(status: string): boolean {
  return ['READY', 'COMPLETED', 'DONE'].includes(status)
}

function isFailed(status: string): boolean {
  return ['FAILED', 'ERROR', 'CANCELLED'].includes(status)
}

function App() {
  const [url, setUrl] = useState('')
  const [projectId, setProjectId] = useState(() => new URLSearchParams(window.location.search).get('project_id') ?? '')
  const [project, setProject] = useState<Project | null>(null)
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])
  const [moments, setMoments] = useState<Moment[]>([])
  const [clips, setClips] = useState<GeneratedClip[]>([])
  const [clipsLoading, setClipsLoading] = useState(true)
  const [clipsLoaded, setClipsLoaded] = useState(false)
  const [clipsError, setClipsError] = useState<string | null>(null)
  const [creatingClipId, setCreatingClipId] = useState<string | null>(null)
  const [clipCreationError, setClipCreationError] = useState<string | null>(null)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [savedProjects, setSavedProjects] = useState<Project[]>([])
  const [savedProjectsLoading, setSavedProjectsLoading] = useState(true)
  const [savedProjectsError, setSavedProjectsError] = useState<string | null>(null)
  const [projectsOffset, setProjectsOffset] = useState(0)
  const [hasMoreProjects, setHasMoreProjects] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [projectError, setProjectError] = useState<string | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [contentError, setContentError] = useState<string | null>(null)
  const [projectLoading, setProjectLoading] = useState(false)
  const [contentLoading, setContentLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState<Selection>(null)
  const youtubePlayerRef = useRef<YouTubePlayer | null>(null)
  const activeProjectId = useRef(projectId)
  const contentLoadedFor = useRef<string | null>(null)
  const currentStatus = statusName(status)

  useEffect(() => {
    activeProjectId.current = projectId
  }, [projectId])

  useEffect(() => {
    let cancelled = false
    setSavedProjectsLoading(true)
    setSavedProjectsError(null)
    setHasMoreProjects(false)
    getProjects()
      .then((projects) => {
        if (!cancelled) {
          setSavedProjects(projects)
          setProjectsOffset(projects.length)
          setHasMoreProjects(projects.length === PROJECTS_PAGE_SIZE)
        }
      })
      .catch((error) => {
        if (!cancelled) setSavedProjectsError(errorMessage(error))
      })
      .finally(() => {
        if (!cancelled) setSavedProjectsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  async function loadMoreProjects() {
    if (savedProjectsLoading) return
    const requestedProjectId = projectId
    setSavedProjectsLoading(true)
    setSavedProjectsError(null)
    try {
      const next = await getProjects(projectsOffset)
      if (activeProjectId.current !== requestedProjectId) return
      setSavedProjects((current) => [...new Map([...current, ...next].map((item) => [item.id, item])).values()])
      setProjectsOffset((offset) => offset + next.length)
      setHasMoreProjects(next.length === PROJECTS_PAGE_SIZE)
    } catch (error) {
      if (activeProjectId.current === requestedProjectId) setSavedProjectsError(errorMessage(error))
    } finally {
      if (activeProjectId.current === requestedProjectId) setSavedProjectsLoading(false)
    }
  }

  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    setProjectLoading(true)
    setClipsLoading(true)
    setContentLoading(contentLoadedFor.current !== projectId)
    const stop = watchProject(projectId, {
      onStatus: (nextStatus) => {
        if (['QUEUED', 'INGESTING', 'TRANSCRIBING', 'ANALYZING'].includes(nextStatus.status ?? '')) {
          contentLoadedFor.current = null
        }
        setStatus(nextStatus)
        setStatusError(null)
        setSavedProjects((current) => current.map((item) => item.id === projectId ? { ...item, status: nextStatus.status } : item))
      },
      onError: (error) => setStatusError(errorMessage(error)),
      onRefresh: async (nextStatus) => {
        const reuseContent = contentLoadedFor.current === projectId
        if (!reuseContent) setContentLoading(true)
        await Promise.all([
          getProject(projectId).then((nextProject) => {
            if (cancelled) return
            setProject(nextProject)
            setProjectError(null)
          }).catch((error) => {
            if (!cancelled) setProjectError(errorMessage(error))
          }).finally(() => {
            if (!cancelled) setProjectLoading(false)
          }),
          reuseContent ? Promise.resolve() : Promise.all([getTranscript(projectId), getMoments(projectId)]).then(([nextTranscript, nextMoments]) => {
            if (cancelled) return
            setTranscript(normalizeTranscript(nextTranscript))
            setMoments(normalizeMoments(nextMoments))
            if (['READY', 'RENDERING', 'FAILED'].includes(nextStatus?.status ?? '')) contentLoadedFor.current = projectId
            setContentError(null)
          }).catch((error) => {
            if (!cancelled) setContentError(errorMessage(error))
          }).finally(() => {
            if (!cancelled) setContentLoading(false)
          }),
          getClips(projectId).then((response) => {
            if (cancelled) return
            setClips(response.clips)
            setClipsLoaded(true)
            setClipsError(null)
          }).catch((error) => {
            if (!cancelled) setClipsError(errorMessage(error))
          }).finally(() => {
            if (!cancelled) setClipsLoading(false)
          }),
        ])
      },
    })
    return () => {
      cancelled = true
      stop()
    }
  }, [projectId, refreshVersion])

  const clipsByMoment = useMemo(() => new Map(clips.map((clip) => [clip.moment_id, clip])), [clips])
  const failed = isFailed(currentStatus)
  const youtubeId = getString(asRecord(project?.video).youtube_id)
  const title = getString(project?.title) ?? getString(asRecord(project?.video).title) ?? 'Untitled project'
  const sourceUrl = getString(project?.source_url) ?? getString(project?.url)
  const statusMessage = failed
    ? publicError(status)
    : ({ QUEUED: 'Waiting for processing.', INGESTING: 'Fetching and preparing the video.',
      TRANSCRIBING: 'Generating the timestamped transcript.', ANALYZING: 'Finding and ranking candidate moments.',
      RENDERING: 'Rendering the selected clip.' } as Record<string, string>)[currentStatus]

  const sortedTranscript = useMemo(
    () => [...transcript].sort((a, b) => (a.start ?? 0) - (b.start ?? 0)),
    [transcript],
  )

  function seekTo(start: number | undefined, end: number | undefined, label: string, id?: string) {
    if (start === undefined) return
    setSelected({ id, start, end, label })
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
    setStatusError(null)
    setContentError(null)
    try {
      const response = await createProject(trimmedUrl)
      const newProjectId = extractProjectId(response)
      if (!newProjectId) throw new Error('The API did not return a project_id.')
      contentLoadedFor.current = null
      setProject(null)
      setStatus(null)
      setTranscript([])
      setMoments([])
      setClips([])
      setClipsLoading(true)
      setClipsLoaded(false)
      setClipsError(null)
      setCreatingClipId(null)
      setClipCreationError(null)
      setSelected(null)
      setProjectId(newProjectId)
      setRefreshVersion((version) => version + 1)
      window.history.replaceState({}, '', `${window.location.pathname}?project_id=${encodeURIComponent(newProjectId)}`)
    } catch (error) {
      setCreateError(errorMessage(error))
    } finally {
      setCreating(false)
    }
  }

  async function handleCreateClip(moment: Moment) {
    const momentId = getString(moment.id)
    const start = moment.start
    const end = moment.end
    if (!momentId || start === undefined || end === undefined || creatingClipId) return
    const requestedProjectId = projectId
    setSelected({ id: momentId, start, end, label: moment.title ?? 'Moment' })
    setCreatingClipId(momentId)
    setClipCreationError(null)
    try {
      const clip = await createClip(requestedProjectId, { moment_id: momentId, start, end })
      if (activeProjectId.current !== requestedProjectId) return
      setClips((current) => [...current.filter((item) => item.id !== clip.id && item.moment_id !== clip.moment_id), clip])
      setStatus({ status: 'RENDERING', message: 'Rendering the selected clip.' })
    } catch (error) {
      if (activeProjectId.current === requestedProjectId) setClipCreationError(errorMessage(error))
    } finally {
      if (activeProjectId.current === requestedProjectId) {
        setCreatingClipId(null)
        // Read the persisted state after success or a conflict; never retry a POST automatically.
        setRefreshVersion((version) => version + 1)
      }
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

      <details className="saved-projects panel">
        <summary>Saved projects</summary>
        {savedProjectsLoading && <p className="muted">Loading saved projects…</p>}
        {savedProjectsError && <p className="error-text" role="alert">Could not load saved projects: {savedProjectsError}</p>}
        {!savedProjectsLoading && !savedProjectsError && savedProjects.length === 0 && <p className="muted">No saved projects yet.</p>}
        <ul className="saved-project-list">
          {savedProjects.map((savedProject) => {
            const id = extractProjectId(savedProject)
            if (!id) return null
            const savedTitle = getString(savedProject.title) ?? getString(asRecord(savedProject.video).title)
              ?? getString(savedProject.source_url) ?? id
            return <li key={id}>
              <a href={`?project_id=${encodeURIComponent(id)}`} aria-current={id === projectId ? 'page' : undefined}>{savedTitle}</a>
              <span className="muted">{getString(savedProject.status)?.toUpperCase() ?? 'UNKNOWN'}</span>
            </li>
          })}
        </ul>
        {hasMoreProjects && <button type="button" className="clip-button" disabled={savedProjectsLoading} onClick={() => void loadMoreProjects()}>
          {savedProjectsLoading ? 'Loading projects…' : 'Load more projects'}
        </button>}
      </details>

      {projectId && (
        <section className="workspace">
          {projectLoading && !project && <div className="panel loading-state">Loading project…</div>}
          {(projectError || statusError) && <div className="alert error-box" role="alert"><strong>Could not load project.</strong> {projectError ?? statusError}</div>}

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
                  <span>{statusMessage ?? 'The API reported a FAILED state. Any saved content is shown below.'}</span>
                </div>
              )}
              {!failed && !isReady(currentStatus) && (
                <div className="alert progress-box">
                  <strong>{currentStatus === 'RENDERING' ? 'Generating clips' : `Processing: ${currentStatus}`}</strong>
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
                {youtubeId ? (
                  <YoutubePlayer videoId={youtubeId} selection={selected} playerRef={youtubePlayerRef} />
                ) : (
                  <div className="empty-state video-unavailable">
                    <strong>No YouTube video ID was returned by the API.</strong>
                    <span>Metadata, transcript, and moments are still shown below. The player will appear when the project has YouTube metadata.</span>
                  </div>
                )}
              </section>

              <div className="content-grid">
                <section className="moments-section panel">
                  <div className="section-heading"><div><p className="eyebrow">AI SUGGESTIONS</p><h2>Suggested moments</h2></div><span className="count">{moments.length}</span></div>
                  {contentLoading && <p className="muted">Loading moments…</p>}
                  {!contentLoading && contentError && <p className="error-text" role="alert">Could not load moments: {contentError}</p>}
                  {!contentLoading && !contentError && moments.length === 0 && <Empty text="No moments generated yet." />}
                  {clipsLoading && <p className="muted clip-loading" role="status">Loading generated clips…</p>}
                  {clipsError && <div className="clip-load-error">
                    <p className="error-text" role="alert">Could not load generated clips: {clipsError}</p>
                    <button type="button" className="text-button" disabled={clipsLoading} onClick={() => setRefreshVersion((version) => version + 1)}>Reload clip status</button>
                  </div>}
                   {clipCreationError && <p className="error-text clip-generation-error" role="alert">Could not create clip: {clipCreationError}</p>}
                   <div className="moment-list">
                    {moments.map((moment, index) => {
                      const start = moment.start
                      const end = moment.end
                      const key = getString(moment.id) ?? `${start ?? index}-${index}`
                       const clip = moment.id ? clipsByMoment.get(moment.id) : undefined
                       const canCreateClip = !!moment.id && start !== undefined && end !== undefined
                         && clipsLoaded && !clipsLoading && !clipsError && (isReady(currentStatus) || isFailed(currentStatus))
                       return <article className={`moment-card ${selected?.id === moment.id || (!selected?.id && selected?.label === (moment.title ?? '')) ? 'selected' : ''}`} key={key}>
                        <div className="moment-topline"><span className="moment-number">{String(index + 1).padStart(2, '0')}</span><span className="time-range">{formatTime(start)} – {formatTime(end)}</span></div>
                        <h3>{moment.title}</h3>
                        {moment.description && <p>{moment.description}</p>}
                        {moment.reason && <p className="reason"><strong>Why it stands out:</strong> {moment.reason}</p>}
                        <div className="moment-footer">
                          {moment.score !== undefined && <span className="score">Score {moment.score}</span>}
                           <button className="text-button" onClick={() => seekTo(start, end, moment.title ?? 'Moment', moment.id)} disabled={start === undefined}>Jump to moment →</button>
                        </div>
                        <MomentClip
                           clip={clip}
                           loading={clipsLoading}
                           unavailable={!!clipsError || !clipsLoaded}
                           title={moment.title ?? 'Moment'}
                           canCreate={canCreateClip}
                           creating={creatingClipId === moment.id}
                           onCreate={() => void handleCreateClip(moment)}
                         />
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

function MomentClip({ clip, loading, unavailable, title, canCreate, creating, onCreate }: {
  clip?: GeneratedClip
  loading: boolean
  unavailable: boolean
  title: string
  canCreate: boolean
  creating: boolean
  onCreate: () => void
}) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewError, setPreviewError] = useState(false)

  useEffect(() => {
    setPreviewOpen(false)
    setPreviewError(false)
  }, [clip?.id, clip?.media_url])

  if (!clip) {
    return <div className="moment-clip">
      <div className="clip-heading">
        <p className="muted">{loading ? 'Loading clip status…' : unavailable ? 'Clip status unavailable.' : 'No clip created yet.'}</p>
        <button type="button" className="clip-button" onClick={onCreate} disabled={!canCreate || creating}>
          {creating ? 'Requesting…' : 'Create Clip'}
        </button>
      </div>
    </div>
  }

  const ready = clip.status === 'READY'
  const canRetry = clip.status === 'FAILED' && canCreate
  const previewId = `clip-preview-${clip.id}`
  return <div className="moment-clip">
    <div className="clip-heading">
      <span className={`clip-status ${ready ? 'ready' : clip.status === 'FAILED' ? 'failed' : ''}`} role="status">Clip: {clip.status}</span>
      <div className="clip-actions">
        {canRetry && <button type="button" className="clip-button" onClick={onCreate} disabled={creating}>{creating ? 'Requesting…' : 'Retry Clip'}</button>}
        {ready && <>
        {clip.media_url && <button
          type="button"
          className="text-button"
          aria-expanded={previewOpen}
          aria-controls={previewId}
          onClick={() => {
            setPreviewError(false)
            setPreviewOpen((open) => !open)
          }}
        >{previewOpen ? 'Close preview' : 'Preview'}</button>}
        {clip.download_url && <a className="text-button" href={clip.download_url} download>Download</a>}
        </>}
      </div>
    </div>
    {(clip.status === 'FAILED' || clip.error_message) && <p className="error-text" role="alert">{publicError(clip, 'rendering')}</p>}
    {ready && !clip.media_url && <p className="muted">Preview unavailable: no media URL was returned.</p>}
    {ready && !clip.download_url && <p className="muted">Download unavailable: no download URL was returned.</p>}
    {ready && clip.media_url && previewOpen && <div className="clip-preview" id={previewId}>
      <video
        className="clip-player"
        controls
        playsInline
        preload="metadata"
        src={clip.media_url}
        aria-label={`Clip preview: ${title}`}
        onError={() => setPreviewError(true)}
      />
      {previewError && <p className="error-text" role="alert">{stageMessages.media}</p>}
    </div>}
  </div>
}

type YouTubePlayer = {
  seekTo: (seconds: number, allowSeekAhead?: boolean) => void
  playVideo: () => void
  pauseVideo: () => void
  getCurrentTime?: () => number
  destroy: () => void
}

type YouTubeApi = {
  Player: new (element: HTMLElement, options: {
    videoId: string
    playerVars?: Record<string, number | string>
    events?: { onReady?: () => void; onError?: (event: { data?: unknown }) => void }
  }) => YouTubePlayer
}

declare global {
  interface Window {
    YT?: YouTubeApi
    onYouTubeIframeAPIReady?: () => void
  }
}

let youtubeApiPromise: Promise<void> | null = null
const PLAYER_LOAD_TIMEOUT_MS = 15000

function youtubeError(code: unknown): string {
  if (code === 101 || code === 150) return 'Embedding is disabled for this video. Open it on YouTube to watch it.'
  if (code === 100) return 'This source video is unavailable in the embedded player. Try opening it on YouTube.'
  if (code === 153) return 'YouTube could not verify this player. Try opening the video on YouTube.'
  return stageMessages.media
}

function loadYouTubeApi(): Promise<void> {
  if (typeof window.YT?.Player === 'function') return Promise.resolve()
  if (youtubeApiPromise) return youtubeApiPromise
  youtubeApiPromise = new Promise<void>((resolve, reject) => {
    const previousReady = window.onYouTubeIframeAPIReady
    const script = document.createElement('script')
    let settled = false
    const finish = (success: boolean) => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      if (window.onYouTubeIframeAPIReady === onReady) window.onYouTubeIframeAPIReady = previousReady
      script.onerror = null
      if (success) resolve()
      else {
        script.remove()
        reject(new Error('The source player API did not load.'))
      }
    }
    const onReady = () => {
      finish(typeof window.YT?.Player === 'function')
      try { previousReady?.() } catch { /* Another consumer must not block our readiness. */ }
    }
    const timer = window.setTimeout(() => finish(false), PLAYER_LOAD_TIMEOUT_MS)
    window.onYouTubeIframeAPIReady = onReady
    script.src = 'https://www.youtube.com/iframe_api'
    script.async = true
    script.onerror = () => finish(false)
    try { document.head.appendChild(script) } catch { finish(false) }
  }).catch((error: unknown) => {
    youtubeApiPromise = null
    throw error
  })
  return youtubeApiPromise
}

function YoutubePlayer({ videoId, selection, playerRef }: {
  videoId: string
  selection: Selection
  playerRef: { current: YouTubePlayer | null }
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)
  const [playerError, setPlayerError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    let failed = false
    let player: YouTubePlayer | null = null
    let readinessTimer: number | undefined
    const container = hostRef.current
    setReady(false)
    setPlayerError(null)
    playerRef.current = null

    const dispose = () => {
      try { player?.destroy() } catch { /* Remove the iframe even if provider cleanup fails. */ }
      if (playerRef.current === player) playerRef.current = null
      player = null
      container?.replaceChildren()
    }
    const fail = (message: string) => {
      if (cancelled || failed) return
      failed = true
      window.clearTimeout(readinessTimer)
      dispose()
      setReady(false)
      setPlayerError(message)
    }

    void loadYouTubeApi().then(() => {
      if (cancelled) return
      if (!container || !window.YT?.Player) { fail(stageMessages.media); return }
      // YouTube replaces its mount with an iframe. Keep that mutable DOM inside
      // a stable React-owned container so retries/unmounts can cleanly recreate it.
      const mount = document.createElement('div')
      container.appendChild(mount)
      readinessTimer = window.setTimeout(() => fail('The source player took too long to load. Retry the player or open the video on YouTube.'), PLAYER_LOAD_TIMEOUT_MS)
      const created = new window.YT.Player(mount, {
        videoId,
        playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
        events: {
          onReady: () => {
            if (!cancelled && !failed) {
              window.clearTimeout(readinessTimer)
              setReady(true)
            }
          },
          onError: (event) => fail(youtubeError(event.data)),
        },
      })
      player = created
      if (cancelled || failed) dispose()
      else playerRef.current = created
    }).catch(() => fail(stageMessages.media))

    return () => {
      cancelled = true
      window.clearTimeout(readinessTimer)
      dispose()
    }
  }, [videoId, playerRef, attempt])

  useEffect(() => {
    const player = playerRef.current
    if (!ready || !player || !selection) return
    player.seekTo(selection.start, true)
    player.playVideo()
  }, [ready, selection, playerRef])

  useEffect(() => {
    const end = selection?.end
    if (!ready || end === undefined) return
    const timer = window.setInterval(() => {
      const player = playerRef.current
      const currentTime = player?.getCurrentTime?.()
      if (player && currentTime !== undefined && currentTime >= end) {
        window.clearInterval(timer)
        player.pauseVideo()
      }
    }, 250)
    return () => window.clearInterval(timer)
  }, [ready, selection, playerRef])

  return <div className="youtube-player">
    <div className="youtube-player-frame" aria-busy={!ready && !playerError}>
      <div ref={hostRef} aria-label="YouTube source video" />
    </div>
    {!ready && !playerError && <p className="muted player-loading" role="status">Loading source player…</p>}
    {playerError && <p className="error-text" role="alert">{playerError}</p>}
    {playerError && <div className="player-actions">
      <button type="button" className="clip-button" onClick={() => setAttempt((value) => value + 1)}>Retry player</button>
      <a className="text-button" href={`https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`} target="_blank" rel="noreferrer">Open on YouTube</a>
    </div>}
  </div>
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
