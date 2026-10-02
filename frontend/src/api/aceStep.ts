import { apiFetch, apiJson } from './http'
import * as v from './nativeValidation'
import { parseAceGenerationSettings, parseAceJobResponse, parseAceJobsResponse, parseAceJobQueryResponse, parseAceJobReleaseResponse } from './contracts'
import type { AceGenerationSettings, AceJobResponse, AceJobReleaseResponse, AceAdoptRequest } from './contracts'

const BASE = '/api/ace'

export interface GenerateMusicRequest {
  use_cot_caption?: boolean
  prompt?: string
  lyrics?: string
  sample_mode?: boolean
  sample_query?: string
  model?: string
  bpm?: number
  key_scale?: string
  time_signature?: string
  vocal_language?: string
  inference_steps?: number
  guidance_scale?: number
  seed?: number
  use_random_seed?: boolean
  audio_duration?: number
  batch_size?: number
  audio_format?: 'mp3' | 'wav' | 'flac'
  task_type?: 'text2music' | 'cover' | 'repaint' | 'extract' | 'lego' | 'complete'
  audio_cover_strength?: number
  repainting_start?: number
  repainting_end?: number
  track_name?: string
  track_classes?: string[]
}

export interface ModelInventoryEntry {
  name: string
  is_default: boolean
  is_loaded: boolean
  supported_task_types: string[]
}

export interface ModelInventory {
  models: ModelInventoryEntry[]
  default_model: string
  lm_models: string[]
  loaded_lm_model: string | null
  llm_initialized: boolean
}

export interface HealthResponse {
  status: string
  service: string
  version: string
  models_initialized: boolean
  llm_initialized: boolean
  loaded_model: string | null
  loaded_lm_model: string | null
}

export type ReleaseTaskResponse = AceJobReleaseResponse
export type QueryResultEntry = AceJobResponse

export interface Envelope<T> {
  data: T
  code: number
  error?: string
  timestamp?: number
  extra?: unknown
}

function isEnvelope<T>(payload: Envelope<T> | T): payload is Envelope<T> {
  return v.isRecord(payload) && 'data' in payload && typeof payload.code === 'number'
}

export function unwrap<T>(payload: Envelope<T> | T): T {
  return isEnvelope(payload) ? payload.data : payload
}

export async function health(signal?: AbortSignal): Promise<HealthResponse> {
  const row = v.record(v.nativePayload(await apiFetch(`${BASE}/health`, { signal })))
  return {
    status: v.string(row.status), service: v.string(row.service), version: v.string(row.version),
    models_initialized: v.boolean(row.models_initialized), llm_initialized: v.boolean(row.llm_initialized),
    loaded_model: v.nullableString(row.loaded_model), loaded_lm_model: v.nullableString(row.loaded_lm_model),
  }
}

export interface InitModelResponse {
  message: string
  slot: number
  loaded_model: string | null
  loaded_lm_model: string | null
}

export async function initModel(initLlm: boolean, lmModelPath?: string): Promise<InitModelResponse> {
  const row = v.record(v.nativePayload(await apiJson(`${BASE}/v1/init`, {
    init_llm: initLlm, lm_model_path: lmModelPath,
  })))
  return { message: v.string(row.message), slot: v.number(row.slot), loaded_model: v.nullableString(row.loaded_model), loaded_lm_model: v.nullableString(row.loaded_lm_model) }
}

export async function modelInventory(signal?: AbortSignal): Promise<ModelInventory> {
  const row = v.record(v.nativePayload(await apiFetch(`${BASE}/v1/model_inventory`, { signal })))
  return {
    models: v.array(row.models, (item) => {
      const model = v.record(item)
      return { name: v.string(model.name), is_default: v.boolean(model.is_default), is_loaded: v.boolean(model.is_loaded), supported_task_types: v.stringArray(model.supported_task_types) }
    }),
    // Native inventory carries LM load metadata; the UI selects by name.
    default_model: row.default_model === null ? '' : v.string(row.default_model),
    lm_models: v.array(row.lm_models, (item) => {
      const model = v.record(item)
      v.boolean(model.is_loaded)
      return v.string(model.name)
    }),
    loaded_lm_model: v.nullableString(row.loaded_lm_model), llm_initialized: v.boolean(row.llm_initialized),
  }
}

export interface StatsResponse {
  jobs: Record<string, number>
  queue_size: number
  queue_maxsize: number
  avg_job_seconds: number
}

export async function stats(): Promise<StatsResponse> {
  const row = v.record(v.nativePayload(await apiFetch(`${BASE}/v1/stats`)))
  const jobs: Record<string, number> = {}
  for (const [key, value] of Object.entries(v.record(row.jobs))) jobs[key] = v.number(value)
  return { jobs, queue_size: v.number(row.queue_size), queue_maxsize: v.number(row.queue_maxsize), avg_job_seconds: v.number(row.avg_job_seconds) }
}

export async function releaseTask(req: GenerateMusicRequest, refAudioFile: File | null | undefined, title: string, voiceId: string | null, styleAudioFile?: File | null, settings?: AceGenerationSettings): Promise<ReleaseTaskResponse> {
  const form = new FormData()
  form.append('params', JSON.stringify(req))
  form.append('title', title)
  if (voiceId) form.append('voice_id', voiceId)
  if (refAudioFile) form.append('ctx_audio', refAudioFile, refAudioFile.name)
  if (styleAudioFile) form.append('ref_audio', styleAudioFile, styleAudioFile.name)
  if (settings) form.append('settings', JSON.stringify(parseAceGenerationSettings(settings)))
  return parseAceJobReleaseResponse(await apiFetch('/api/ace-jobs', { method: 'POST', body: form }))
}

export async function listJobs(): Promise<AceJobResponse[]> {
  return parseAceJobsResponse(await apiFetch('/api/ace-jobs')).jobs
}

export async function adoptLegacyJob(request: AceAdoptRequest): Promise<AceJobResponse> {
  return parseAceJobResponse(await apiJson('/api/ace-jobs/adopt', request))
}

export async function queryResult(taskIds: string[], signal?: AbortSignal): Promise<QueryResultEntry[]> {
  const raw = await apiFetch('/api/ace-jobs/query', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task_id_list: taskIds }), signal,
  })
  return parseAceJobQueryResponse(raw).data
}

export async function cancelTask(taskId: string): Promise<AceJobResponse> {
  return parseAceJobResponse(await apiJson(`/api/ace-jobs/${encodeURIComponent(taskId)}/cancel`, {}))
}

export async function cancelAllTasks(): Promise<void> {
  await apiJson('/api/ace-jobs/cancel-all', {})
}

export async function retrySave(taskId: string): Promise<AceJobResponse> {
  return parseAceJobResponse(await apiJson(`/api/ace-jobs/${encodeURIComponent(taskId)}/retry-save`, {}))
}

export async function deleteJob(taskId: string): Promise<void> {
  await apiFetch(`/api/ace-jobs/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

export async function loraLoad(loraPath: string, adapterName?: string): Promise<void> {
  await apiJson(`${BASE}/v1/lora/load`, { lora_path: loraPath, adapter_name: adapterName })
}

export async function loraUnload(): Promise<void> {
  await apiJson(`${BASE}/v1/lora/unload`, {})
}

export async function loraToggle(useLora: boolean): Promise<void> {
  await apiJson(`${BASE}/v1/lora/toggle`, { use_lora: useLora })
}

export async function loraScale(scale: number): Promise<void> {
  await apiJson(`${BASE}/v1/lora/scale`, { scale })
}

export function audioUrl(path: string): string {
  return `${BASE}/v1/audio?path=${encodeURIComponent(path)}`
}
