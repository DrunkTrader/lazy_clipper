import { ApiError, FailureFields, FailureStage } from './errors'

export type JsonObject = Record<string, unknown>

export type Project = JsonObject & FailureFields & {
  project_id?: string
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

export type Moment = JsonObject & {
  id?: string
  title?: string
  description?: string
  start?: number
  end?: number
  score?: number
  reason?: string
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

async function request<T>(path: string, stage: FailureStage, init?: RequestInit): Promise<T> {
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
    return parsed as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(0, null, stage)
  } finally {
    clearTimeout(timeout)
  }
}

export function createProject(url: string): Promise<JsonObject> {
  return request<JsonObject>('/api/v1/ingest', 'ingestion', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export function getProjects(): Promise<Project[]> {
  return request<Project[]>('/api/v1/projects', 'database')
}

export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, 'database')
}

export function getProjectStatus(projectId: string): Promise<ProjectStatus> {
  return request<ProjectStatus>(`/api/v1/projects/${encodeURIComponent(projectId)}/status`, 'database')
}

export function getTranscript(projectId: string): Promise<unknown> {
  return request<unknown>(`/api/v1/projects/${encodeURIComponent(projectId)}/transcript`, 'database')
}

export function getMoments(projectId: string): Promise<unknown> {
  return request<unknown>(`/api/v1/projects/${encodeURIComponent(projectId)}/moments`, 'database')
}

export function getClips(projectId: string): Promise<ClipsResponse> {
  return request<ClipsResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/clips`, 'media')
}

export function createClip(projectId: string, payload: { moment_id: string; start: number; end: number }): Promise<GeneratedClip> {
  return request<GeneratedClip>(`/api/v1/projects/${encodeURIComponent(projectId)}/clips`, 'rendering', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function asRecord(value: unknown): JsonObject {
  return typeof value === 'object' && value !== null ? (value as JsonObject) : {}
}

export function getString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

export function getNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value)
  return undefined
}

export function extractProjectId(value: unknown): string | undefined {
  const record = asRecord(value)
  return getString(record.project_id) ?? getString(record.id)
}

export function extractItems(value: unknown, keys: string[]): JsonObject[] {
  if (Array.isArray(value)) return value.filter((item): item is JsonObject => typeof item === 'object' && item !== null)
  const record = asRecord(value)
  for (const key of keys) {
    const items = record[key]
    if (Array.isArray(items)) {
      return items.filter((item): item is JsonObject => typeof item === 'object' && item !== null)
    }
  }
  return []
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
