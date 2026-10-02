import { apiFetch } from './http'
import { parseYueJobResponse, parseYueJobsResponse, parseYueSubmitRequest, parseYueOptions, parseDeleteYueJobResponse } from './contracts'
import type { YueJobResponse, YueSubmitRequest, YueOptions } from './contracts'
import type { GenerateOptions } from './yue2'
export function completeYueOptions(options: GenerateOptions): YueOptions {
  return parseYueOptions({ cfg_scale: null, num_inference_steps: 8, abc: null,
    semantic_temperature: null, semantic_top_p: null, semantic_top_k: null, semantic_repetition_penalty: null,
    semantic_penalty_window: null, semantic_min_tokens: null, semantic_max_tokens: null,
    abc_temperature: null, abc_top_p: null, abc_top_k: null, abc_repetition_penalty: null,
    abc_penalty_window: null, abc_min_tokens: null, abc_max_tokens: null, ...options })
}
function decodeJob(value: unknown): YueJobResponse {
  const job = parseYueJobResponse(value)
  const progress = job.progress
  if (progress && (progress.run_id !== job.id || progress.current > (progress.total ?? Infinity) || progress.started_ms > progress.phase_started_ms || progress.phase_started_ms > progress.updated_ms)) throw new TypeError('Invalid native progress')
  return job
}
export async function list(signal?: AbortSignal): Promise<YueJobResponse[]> {
  const response = await apiFetch('/api/yue-jobs', { signal }, parseYueJobsResponse)
  return response.jobs.map(decodeJob)
}
export async function submit(request: YueSubmitRequest, signal?: AbortSignal): Promise<YueJobResponse> {
  return apiFetch('/api/yue-jobs', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseYueSubmitRequest(request)) }, decodeJob)
}
export function cancel(id: string, signal?: AbortSignal): Promise<YueJobResponse> {
  return apiFetch(`/api/yue-jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: '{}' }, decodeJob)
}

export async function remove(id: string, signal?: AbortSignal) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid job ID')
  return apiFetch(`/api/yue-jobs/${id}`, { method: 'DELETE', signal }, parseDeleteYueJobResponse)
}
