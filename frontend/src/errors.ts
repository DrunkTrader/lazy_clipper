// Display only application-owned strings, even when an old API or proxy returns
// raw exception text, HTML, or a misleading error.message.
import errorCatalog from './error-catalog.json'

export const stageMessages = errorCatalog.stageMessages

export type FailureStage = keyof typeof stageMessages
const interruptedMessages: Partial<Record<FailureStage, string>> = errorCatalog.interruptedMessages
const captionMessages: Record<string, string> = errorCatalog.captionMessages
const timeoutMessages: Partial<Record<FailureStage, string>> = errorCatalog.timeoutMessages
const limitMessages: Record<string, string> = errorCatalog.limitMessages

export type FailureFields = {
  failed_stage?: FailureStage | null
  error?: { code: string; message?: string } | null
}

const clientMessages: Record<string, string> = errorCatalog.clientMessages

export function publicError(value: unknown, fallback: FailureStage = 'unknown', allowClientError = false): string {
  const record = typeof value === 'object' && value !== null ? value as Record<string, unknown> : {}
  const error = typeof record.error === 'object' && record.error !== null ? record.error as Record<string, unknown> : {}
  if (allowClientError && typeof error.code === 'string' && Object.prototype.hasOwnProperty.call(clientMessages, error.code)) {
    return clientMessages[error.code]
  }
  const stage = typeof record.failed_stage === 'string' && Object.prototype.hasOwnProperty.call(stageMessages, record.failed_stage)
    ? record.failed_stage as FailureStage : fallback
  if (error.code === 'PROCESSING_INTERRUPTED' && interruptedMessages[stage]) return interruptedMessages[stage]
  if (error.code === 'PROCESSING_TIMEOUT' && timeoutMessages[stage]) return timeoutMessages[stage]
  if (typeof error.code === 'string' && Object.prototype.hasOwnProperty.call(limitMessages, error.code)) return limitMessages[error.code]
  if (typeof error.code === 'string' && Object.prototype.hasOwnProperty.call(captionMessages, error.code)) return captionMessages[error.code]
  return stageMessages[stage]
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, body: unknown, fallback: FailureStage) {
    super(status === 0 ? 'Unable to reach the server. Please try again.' : publicError(body, fallback, (status >= 400 && status < 500) || status === 507))
    this.name = 'ApiError'
    this.status = status
  }
}

export function errorMessage(error: unknown, fallback: FailureStage = 'unknown'): string {
  return error instanceof ApiError ? error.message : stageMessages[fallback]
}
