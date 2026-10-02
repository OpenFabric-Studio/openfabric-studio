import type { ModelRuntimeStatus } from '../../types'

export type DisplayEngineStatus = ModelRuntimeStatus | 'unknown'
export const ENGINE_STATUS_CLASSES: Record<DisplayEngineStatus, string> = {
  stopped: 'bg-status-cancelled', unknown: 'bg-status-cancelled',
  starting: 'bg-status-queued motion-safe:animate-pulse', stopping: 'bg-status-queued motion-safe:animate-pulse',
  running: 'bg-status-done', error: 'bg-status-failed',
}
export const ENGINE_STATUS_KEYS: Record<DisplayEngineStatus, string> = {
  stopped: 'modelStatus.stopped', starting: 'modelStatus.starting', running: 'modelStatus.running',
  stopping: 'modelStatus.stopping', error: 'modelStatus.error', unknown: 'appNavigation.statusUnknown',
}
