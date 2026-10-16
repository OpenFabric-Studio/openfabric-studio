import { apiFetch } from './http'
import { parseTrainingCaptionJob, parseTrainingCaptionJobs, parseTrainingCaptionRequest } from './generated'
import type { TrainingCaptionJob, TrainingCaptionJobs, TrainingCaptionRequest } from './generated'

const base = '/api/music-lora/captions/jobs'
function jobPath(id: string): string {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid caption job ID')
  return `${base}/${id}`
}
export function listTrainingCaptions(signal?: AbortSignal): Promise<TrainingCaptionJobs> { return apiFetch(base, { signal }, parseTrainingCaptionJobs) }
export function submitTrainingCaptions(request: TrainingCaptionRequest, signal?: AbortSignal): Promise<TrainingCaptionJob> {
  return apiFetch(base, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseTrainingCaptionRequest(request)) }, parseTrainingCaptionJob)
}
export function getTrainingCaptions(id: string, signal?: AbortSignal): Promise<TrainingCaptionJob> { return apiFetch(jobPath(id), { signal }, parseTrainingCaptionJob) }
export function cancelTrainingCaptions(id: string, signal?: AbortSignal): Promise<TrainingCaptionJob> { return apiFetch(`${jobPath(id)}/cancel`, { method: 'POST', signal }, parseTrainingCaptionJob) }
