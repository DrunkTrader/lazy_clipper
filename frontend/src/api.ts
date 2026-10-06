import { ApiError, FailureFields, FailureStage } from './errors'
import { isClip, isClips, isIngest, isMoments, isProject, isProjects, isProjectStatus, isTranscript } from './contracts'

export type JsonObject = Record<string, unknown>

export type IngestResponse = {
  project_id: string
  status: string
}

export type Project = JsonObject & FailureFields & {
  id?: string
  title?: string
  source_url?: string
  status?: string
  created_at?: string
  updated_at?: string
}

export type ProjectStatus = JsonObject & FailureFields & {
  status?: string
  message?: string
}

export type TranscriptSegment = JsonObject & {
  id?: string
  start?: number
  end?: number
  text?: string
  speaker?: string
}

export type TranscriptResponse = {
  project_id: string
  segments: TranscriptSegment[]
}

export type Moment = JsonObject & {
  id?: string
  title?: string
  description?: string
  start?: number
  end?: number
  score?: number
  reason?: string
}

export type MomentsResponse = {
  project_id: string
  moments: Moment[]
}

export type ClipStatus = 'QUEUED' | 'RENDERING' | 'READY' | 'FAILED'

export type GeneratedClip = FailureFields & {
  id: string
  project_id: string
  moment_id: string
  start: number
  end: number
  status: ClipStatus
  error_message: string | null
  media_url: string | null
  download_url: string | null
}

export type ClipsResponse = {
  project_id: string
  clips: GeneratedClip[]
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export function resolveApiMediaUrl(value: string | null): string | null {
  if (value === null || !value.startsWith('/')) return value
  return API_BASE_URL ? `${API_BASE_URL}${value}` : value
}

function resolveClip(clip: GeneratedClip): GeneratedClip {
  return { ...clip, media_url: resolveApiMediaUrl(clip.media_url), download_url: resolveApiMediaUrl(clip.download_url) }
}

async function request<T>(path: string, stage: FailureStage, valid: (value: unknown) => value is T, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 30000)
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
    if (response.status === 204) {
      if (!valid(undefined)) throw new ApiError(500, null, stage)
      return undefined as T
    }
    const body = await response.text()
    let parsed: unknown
    try {
      parsed = JSON.parse(body)
    } catch {
      parsed = undefined
    }
    if (!response.ok || parsed === undefined) {
      throw new ApiError(response.ok ? 500 : response.status, parsed, stage)
    }
    if (!valid(parsed)) throw new ApiError(500, null, stage)
    return parsed
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(0, null, stage)
  } finally {
    clearTimeout(timeout)
  }
}

export function createProject(url: string): Promise<IngestResponse> {
  return request<IngestResponse>('/api/v1/ingest', 'ingestion', isIngest, {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export const PROJECTS_PAGE_SIZE = 25

export function getProjects(offset = 0, limit = PROJECTS_PAGE_SIZE): Promise<Project[]> {
  return request<Project[]>(`/api/v1/projects?limit=${limit}&offset=${offset}`, 'database', isProjects)
}

export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, 'database',
    (value): value is Project => isProject(value) && value.id === projectId)
}

export function getProjectStatus(projectId: string): Promise<ProjectStatus> {
  return request<ProjectStatus>(`/api/v1/projects/${encodeURIComponent(projectId)}/status`, 'database',
    (value): value is ProjectStatus => isProjectStatus(value) && value.project_id === projectId)
}

export function getTranscript(projectId: string): Promise<TranscriptResponse> {
  return request<TranscriptResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/transcript?include_words=false`, 'database',
    (value): value is TranscriptResponse => isTranscript(value) && value.project_id === projectId)
}

export function getMoments(projectId: string): Promise<MomentsResponse> {
  return request<MomentsResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/moments`, 'database',
    (value): value is MomentsResponse => isMoments(value) && value.project_id === projectId)
}

export function getClips(projectId: string): Promise<ClipsResponse> {
  return request<ClipsResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/clips`, 'media',
    (value): value is ClipsResponse => isClips(value) && value.project_id === projectId).then((response) => ({
      ...response, clips: response.clips.map(resolveClip),
    }))
}

export function createClip(projectId: string, payload: { moment_id: string; start: number; end: number }): Promise<GeneratedClip> {
  return request<GeneratedClip>(`/api/v1/projects/${encodeURIComponent(projectId)}/clips`, 'rendering',
    (value): value is GeneratedClip => isClip(value) && value.project_id === projectId && value.moment_id === payload.moment_id, {
    method: 'POST',
    body: JSON.stringify(payload),
  }).then(resolveClip)
}

export function deleteProject(projectId: string): Promise<void> {
  return request<void>(`/api/v1/projects/${encodeURIComponent(projectId)}`, 'database',
    (value): value is void => value === undefined, { method: 'DELETE' })
}

export function asRecord(value: unknown): JsonObject {
  return typeof value === 'object' && value !== null ? (value as JsonObject) : {}
}

export function getString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

export function formatTime(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds)) return '—'
  const safeSeconds = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const remainder = safeSeconds % 60
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}
