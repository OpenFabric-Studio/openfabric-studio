import { apiFetch, apiJson } from './http'
import type { ModelId, OrchestratorStatus } from '../types'

import { parseOrchestratorConfigResponse, parseOrchestratorStatusResponse } from './contracts'
import type { OrchestratorConfigResponse } from './contracts'
export type { Yue2ModelSpecConfig } from './contracts'
export type OrchestratorConfig = OrchestratorConfigResponse

export function getConfig(): Promise<OrchestratorConfig> {
  return apiFetch('/api/orchestrator/config', undefined, parseOrchestratorConfigResponse)
}

export function getStatus(signal?: AbortSignal): Promise<OrchestratorStatus> {
  return apiFetch('/api/orchestrator/status', { signal }, parseOrchestratorStatusResponse)
}

export function switchModel(model: ModelId): Promise<OrchestratorStatus> {
  return apiJson('/api/orchestrator/switch', { model }, 'POST', parseOrchestratorStatusResponse)
}

export function stopActive(): Promise<OrchestratorStatus> {
  return apiJson('/api/orchestrator/stop', {}, 'POST', parseOrchestratorStatusResponse)
}
