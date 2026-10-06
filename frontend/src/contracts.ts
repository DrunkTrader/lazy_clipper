// Validate the fields consumed by the workspace; tolerate additive API fields.
import type { ClipsResponse, GeneratedClip, IngestResponse, JsonObject, Moment, MomentsResponse, Project, ProjectStatus, TranscriptResponse, TranscriptSegment } from './api'
import { stageMessages } from './errors'

const projectStates = ['QUEUED', 'INGESTING', 'TRANSCRIBING', 'ANALYZING', 'RENDERING', 'READY', 'FAILED']
const clipStates = ['QUEUED', 'RENDERING', 'READY', 'FAILED']
const object = (value: unknown): value is JsonObject => typeof value === 'object' && value !== null && !Array.isArray(value)
const string = (value: unknown): value is string => typeof value === 'string'
const identity = (value: unknown): value is string => string(value) && value.trim().length > 0
const nullableString = (value: unknown) => value === undefined || value === null || string(value)
const number = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const state = (value: unknown) => string(value) && projectStates.includes(value)
const date = (value: unknown) => string(value) && Number.isFinite(Date.parse(value))
const range = (value: JsonObject) => number(value.start) && number(value.end) && value.start >= 0 && value.end > value.start

function url(value: unknown, relative = false): boolean {
  if (!identity(value)) return false
  if (relative && value.startsWith('/') && !value.startsWith('//')) return true
  try { return ['http:', 'https:'].includes(new URL(value).protocol) } catch { return false }
}

function failure(value: JsonObject): boolean {
  return (value.failed_stage == null || (string(value.failed_stage) && Object.prototype.hasOwnProperty.call(stageMessages, value.failed_stage)))
    && (value.error == null || (object(value.error) && identity(value.error.code) && nullableString(value.error.message)))
    && nullableString(value.error_message)
}

export function isProject(value: unknown): value is Project {
  if (!object(value) || !identity(value.id) || !state(value.status) || !url(value.source_url)
    || !nullableString(value.title) || !nullableString(value.status_message) || !failure(value)
    || !date(value.created_at) || !date(value.updated_at)) return false
  const video = value.video
  return video == null || (object(video) && identity(video.id) && nullableString(video.title)
    && nullableString(video.youtube_id) && (video.duration == null || (number(video.duration) && video.duration >= 0)))
}

export const isProjects = (value: unknown): value is Project[] => Array.isArray(value) && value.every(isProject)

export function isProjectStatus(value: unknown): value is ProjectStatus {
  return object(value) && identity(value.project_id) && state(value.status) && nullableString(value.message) && failure(value)
}

export function isIngest(value: unknown): value is IngestResponse {
  return object(value) && identity(value.project_id) && string(value.status) && state(value.status.toUpperCase())
}

export function isClip(value: unknown): value is GeneratedClip {
  return object(value) && identity(value.id) && identity(value.project_id) && identity(value.moment_id)
    && range(value) && string(value.status) && clipStates.includes(value.status) && failure(value)
    && (value.media_url === null || url(value.media_url, true))
    && (value.download_url === null || url(value.download_url, true))
    && (value.error_message === null || string(value.error_message))
}

function envelope(value: unknown, key: string, item: (value: unknown) => boolean): value is JsonObject {
  return object(value) && identity(value.project_id) && Array.isArray(value[key]) && value[key].every(item)
}

export function isClips(value: unknown): value is ClipsResponse {
  return envelope(value, 'clips', isClip)
    && (value.clips as GeneratedClip[]).every((clip) => clip.project_id === value.project_id)
}

function segment(value: unknown): value is TranscriptSegment {
  return object(value) && identity(value.id) && range(value) && string(value.text) && nullableString(value.speaker)
}

function moment(value: unknown): value is Moment {
  return object(value) && identity(value.id) && range(value) && string(value.title)
    && string(value.description) && string(value.reason) && number(value.score)
}

export const isTranscript = (value: unknown): value is TranscriptResponse => envelope(value, 'segments', segment)
export const isMoments = (value: unknown): value is MomentsResponse => envelope(value, 'moments', moment)
