// Display only application-owned strings, even when an old API or proxy returns
// raw exception text, HTML, or a misleading error.message.
export const stageMessages = {
  ingestion: 'Internal server error during video ingestion.',
  fetching: 'Internal server error while fetching video data.',
  transcription: 'Internal server error during transcription.',
  analysis: 'Internal server error during AI analysis.',
  rendering: 'Internal server error while generating the clip.',
  media: 'Internal server error while loading media.',
  database: 'Internal server error while accessing project data.',
  unknown: 'Internal server error. Please try again later.',
} as const

export type FailureStage = keyof typeof stageMessages
export type FailureFields = {
  failed_stage?: FailureStage | null
  error?: { code: string; message?: string } | null
}

const clientMessages: Record<string, string> = {
  INVALID_REQUEST: 'Invalid request. Please check your input.',
  INVALID_URL: 'Enter a valid YouTube video URL.',
  INVALID_RANGE: 'Clip end must be greater than clip start.',
  RANGE_EXCEEDS_VIDEO: 'Clip timestamps exceed the source video duration',
  PROJECT_NOT_FOUND: 'Project not found',
  MOMENT_NOT_FOUND: 'Moment not found in this project',
  CLIP_NOT_FOUND: 'Clip not found in this project',
  PROJECT_BUSY: 'Project processing is already in progress',
  ANALYSIS_PENDING: 'Project analysis is not complete',
  SOURCE_MISSING: 'The downloaded source video is missing',
  CLIP_BUSY: 'This clip is already being rendered',
  CLIP_PENDING: 'Clip is not ready',
  CLIP_MISSING: 'Clip file is not available',
}

export function publicError(value: unknown, fallback: FailureStage = 'unknown', allowClientError = false): string {
  const record = typeof value === 'object' && value !== null ? value as Record<string, unknown> : {}
  const error = typeof record.error === 'object' && record.error !== null ? record.error as Record<string, unknown> : {}
  if (allowClientError && typeof error.code === 'string' && Object.prototype.hasOwnProperty.call(clientMessages, error.code)) {
    return clientMessages[error.code]
  }
  const stage = typeof record.failed_stage === 'string' && Object.prototype.hasOwnProperty.call(stageMessages, record.failed_stage)
    ? record.failed_stage as FailureStage : fallback
  return stageMessages[stage]
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, body: unknown, fallback: FailureStage) {
    super(status === 0 ? 'Unable to reach the server. Please try again.' : publicError(body, fallback, status >= 400 && status < 500))
    this.name = 'ApiError'
    this.status = status
  }
}

export function errorMessage(error: unknown, fallback: FailureStage = 'unknown'): string {
  return error instanceof ApiError ? error.message : stageMessages[fallback]
}
