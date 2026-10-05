import { getProjectStatus, ProjectStatus } from './api'
import { ApiError } from './errors'

export const STALE_STATUS_MS = 10 * 60 * 1000

// A single status request drives updates. Refresh boundaries receive the observed
// status so the workspace can reuse saved content during clip-only updates.
export function watchProject(projectId: string, callbacks: {
  onStatus: (status: ProjectStatus) => void
  onError: (error: unknown) => void
  onRefresh: (status: ProjectStatus | null) => Promise<void>
  onStale?: () => void
}): () => void {
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let staleTimer: ReturnType<typeof setTimeout> | undefined
  let lastStatus: string | null = null

  function scheduleStale(status: ProjectStatus) {
    const name = (status.status ?? '').toUpperCase()
    const terminal = ['READY', 'COMPLETED', 'DONE', 'FAILED', 'ERROR', 'CANCELLED'].includes(name)
    if (terminal) {
      clearTimeout(staleTimer)
      staleTimer = undefined
      lastStatus = name
      return
    }
    if (name !== lastStatus) {
      clearTimeout(staleTimer)
      lastStatus = name
      staleTimer = setTimeout(() => {
        staleTimer = undefined
        if (!stopped) callbacks.onStale?.()
      }, STALE_STATUS_MS)
    }
  }

  async function poll(initial: boolean) {
    let terminal = false
    try {
      const status = await getProjectStatus(projectId)
      if (stopped) return
      callbacks.onStatus(status)
      scheduleStale(status)
      terminal = ['READY', 'COMPLETED', 'DONE', 'FAILED', 'ERROR', 'CANCELLED'].includes((status.status ?? '').toUpperCase())
      if (initial || terminal) await callbacks.onRefresh(status)
    } catch (error) {
      if (stopped) return
      callbacks.onError(error)
      terminal = error instanceof ApiError && error.status >= 400 && error.status < 500
      if (initial) await callbacks.onRefresh(null)
    } finally {
      if (!stopped && !terminal) timer = setTimeout(() => void poll(false), 4000)
    }
  }

  void poll(true)
  return () => {
    stopped = true
    clearTimeout(timer)
    clearTimeout(staleTimer)
  }
}
