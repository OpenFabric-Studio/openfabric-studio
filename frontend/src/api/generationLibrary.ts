import { apiFetch } from './http'
import {
  parseGenerationHistoryResponse, parseGenerationPresetsResponse, parseGenerationPreset,
  parseGenerationLibrarySettings, parseUpdateGenerationLibrarySettingsRequest,
  parseCreateGenerationPresetRequest, parseUpdateGenerationPresetRequest,
  parseDuplicateGenerationPresetRequest, parseImportGenerationPresetsRequest,
  parseGenerationPresetImportResponse, parseGenerationDeleteResponse,
} from './contracts'
import type {
  GenerationHistoryEntry, GenerationHistoryResponse, GenerationPresetsResponse, GenerationPreset, GenerationLibrarySettings,
  UpdateGenerationLibrarySettingsRequest, CreateGenerationPresetRequest, UpdateGenerationPresetRequest,
  DuplicateGenerationPresetRequest, ImportGenerationPresetsRequest, GenerationPresetImportResponse,
} from './contracts'
export type GenerationSnapshot = GenerationHistoryEntry['settings']
export type GenerationEngine = GenerationHistoryEntry['engine']
export type { GenerationHistoryEntry, GenerationPreset, GenerationLibrarySettings } from './contracts'
const BASE = '/api/generation'
export interface GenerationFilter { engine?: GenerationEngine; search?: string; offset?: number }
function query(filter: GenerationFilter): string {
  const params = new URLSearchParams()
  if (filter.engine) params.set('engine', filter.engine)
  if (filter.search?.trim()) params.set('search', filter.search.trim())
  params.set('limit', '100'); params.set('offset', String(filter.offset ?? 0))
  return params.size ? `?${params}` : ''
}
export function listHistory(filter: GenerationFilter = {}, signal?: AbortSignal): Promise<GenerationHistoryResponse> {
  return apiFetch(`${BASE}/history${query(filter)}`, { signal }, parseGenerationHistoryResponse)
}
export async function listPresets(filter: GenerationFilter = {}, signal?: AbortSignal): Promise<GenerationPresetsResponse> {
  return apiFetch(`${BASE}/presets${query(filter)}`, { signal }, parseGenerationPresetsResponse)
}
export function getSettings(signal?: AbortSignal): Promise<GenerationLibrarySettings> {
  return apiFetch(`${BASE}/settings`, { signal }, parseGenerationLibrarySettings)
}
async function write<T>(url: string, body: unknown, method: string, parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  return apiFetch(url, { method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, parse)
}
export async function updateSettings(request: UpdateGenerationLibrarySettingsRequest, signal?: AbortSignal): Promise<GenerationLibrarySettings> {
  return write(`${BASE}/settings`, parseUpdateGenerationLibrarySettingsRequest(request), 'PUT', parseGenerationLibrarySettings, signal)
}
export async function createPreset(request: CreateGenerationPresetRequest, signal?: AbortSignal): Promise<GenerationPreset> {
  return write(`${BASE}/presets`, parseCreateGenerationPresetRequest(request), 'POST', parseGenerationPreset, signal)
}
export async function updatePreset(id: string, request: UpdateGenerationPresetRequest, signal?: AbortSignal): Promise<GenerationPreset> {
  return write(`${BASE}/presets/${encodeURIComponent(id)}`, parseUpdateGenerationPresetRequest(request), 'PUT', parseGenerationPreset, signal)
}
export async function duplicatePreset(id: string, request: DuplicateGenerationPresetRequest, signal?: AbortSignal): Promise<GenerationPreset> {
  return write(`${BASE}/presets/${encodeURIComponent(id)}/duplicate`, parseDuplicateGenerationPresetRequest(request), 'POST', parseGenerationPreset, signal)
}
export async function importPresets(request: ImportGenerationPresetsRequest, signal?: AbortSignal): Promise<GenerationPresetImportResponse> {
  return write(`${BASE}/presets/import`, parseImportGenerationPresetsRequest(request), 'POST', parseGenerationPresetImportResponse, signal)
}
export async function deletePreset(id: string, revision: number, signal?: AbortSignal): Promise<void> {
  await apiFetch(`${BASE}/presets/${encodeURIComponent(id)}?revision=${revision}`, { method: 'DELETE', signal }, parseGenerationDeleteResponse)
}
export async function deleteHistory(id: string, signal?: AbortSignal): Promise<void> {
  await apiFetch(`${BASE}/history/${encodeURIComponent(id)}`, { method: 'DELETE', signal }, parseGenerationDeleteResponse)
}

export function getPreset(id: string, signal?: AbortSignal): Promise<GenerationPreset> {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid preset ID')
  return apiFetch(`${BASE}/presets/${id}`, { signal }, parseGenerationPreset)
}
