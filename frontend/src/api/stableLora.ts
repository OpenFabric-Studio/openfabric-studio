import { apiFetch } from './http'
import { parseStableLoraTrainingStatus, parseStableLoraTrainingJob, parseStableLoraTrainingJobs, parseStableLoraTrainingRequest } from './generated'
import type { StableLoraTrainingStatus, StableLoraTrainingJob, StableLoraTrainingJobs, StableLoraTrainingRequest } from './generated'
const base = '/api/music-lora/stable-training'
function jobPath(id: string): string {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid Stable training job ID')
  return `${base}/jobs/${id}`
}
export function stableLoraStatus(signal?: AbortSignal): Promise<StableLoraTrainingStatus> { return apiFetch(`${base}/status`, { signal }, parseStableLoraTrainingStatus) }
export function listStableLora(signal?: AbortSignal): Promise<StableLoraTrainingJobs> { return apiFetch(`${base}/jobs`, { signal }, parseStableLoraTrainingJobs) }
export async function submitStableLora(request: StableLoraTrainingRequest, signal?: AbortSignal): Promise<StableLoraTrainingJob> {
  return apiFetch(`${base}/jobs`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseStableLoraTrainingRequest(request)) }, parseStableLoraTrainingJob)
}
export async function getStableLora(id: string, signal?: AbortSignal): Promise<StableLoraTrainingJob> { return apiFetch(jobPath(id), { signal }, parseStableLoraTrainingJob) }
export async function cancelStableLora(id: string, signal?: AbortSignal): Promise<StableLoraTrainingJob> { return apiFetch(`${jobPath(id)}/cancel`, { method: 'POST', signal }, parseStableLoraTrainingJob) }
