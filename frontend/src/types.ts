export type ModelId = 'ace_step' | 'yue2'
export type TrackOrigin = import('./api/generated').SavedTrack['model']

export type ModelRuntimeStatus = 'stopped' | 'starting' | 'running' | 'stopping' | 'error'

export interface ModelStatusEntry {
  id: ModelId
  label: string
  status: ModelRuntimeStatus
  error: string | null
}

export interface OrchestratorStatus {
  active_model: ModelId | null
  models: Record<ModelId, ModelStatusEntry>
}

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
