import { getProjectStatus, ProjectStatus } from './api'
import { ApiError } from './errors'

// A single status request drives updates. Refresh boundaries receive the observed
// status so the workspace can reuse saved content during clip-only updates.
export function watchProject(projectId: string, callbacks: {
  onStatus: (status: ProjectStatus) => void
  onError: (error: unknown) => void
  onRefresh: (status: ProjectStatus | null) => Promise<void>
}): () => void {
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined

  async function poll(initial: boolean) {
    let terminal = false
    try {
      const status = await getProjectStatus(projectId)
      if (stopped) return
      callbacks.onStatus(status)
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
  }
}
