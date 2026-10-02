export type JsonObject = Record<string, unknown>

export type Project = JsonObject & {
  project_id?: string
  id?: string
  title?: string
  source_url?: string
  status?: string
  created_at?: string
  updated_at?: string
}

export type ProjectStatus = JsonObject & {
  status?: string
  message?: string
  error?: string
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

export type GeneratedClip = {
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

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError(`Could not reach the API at ${API_BASE_URL}.`, 0)
  }

  const body = await response.text()
  let parsed: unknown = undefined
  if (body) {
    try {
      parsed = JSON.parse(body)
    } catch {
      parsed = undefined
    }
  }

  if (!response.ok) {
    const detail =
      typeof parsed === 'object' && parsed !== null
        ? (parsed as JsonObject).detail ?? (parsed as JsonObject).message
        : undefined
    const message = typeof detail === 'string' ? detail : `API request failed (${response.status}).`
    throw new ApiError(message, response.status)
  }

  return parsed as T
}

export function createProject(url: string): Promise<JsonObject> {
  return request<JsonObject>('/api/v1/ingest', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export function getProjects(): Promise<Project[]> {
  return request<Project[]>('/api/v1/projects')
}

export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`)
}

export function getProjectStatus(projectId: string): Promise<ProjectStatus> {
  return request<ProjectStatus>(`/api/v1/projects/${encodeURIComponent(projectId)}/status`)
}

export function getTranscript(projectId: string): Promise<unknown> {
  return request<unknown>(`/api/v1/projects/${encodeURIComponent(projectId)}/transcript`)
}

export function getMoments(projectId: string): Promise<unknown> {
  return request<unknown>(`/api/v1/projects/${encodeURIComponent(projectId)}/moments`)
}

export function getClips(projectId: string): Promise<ClipsResponse> {
  return request<ClipsResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/clips`)
}

export function generateClips(projectId: string): Promise<{ project_id: string; status: 'rendering' }> {
  return request(`/api/v1/projects/${encodeURIComponent(projectId)}/clips`, { method: 'POST' })
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
