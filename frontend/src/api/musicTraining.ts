import { apiFetch } from './http'
import { parseMusicLoraTrainingStatuses, parseMusicLoraTrainingJob, parseMusicLoraTrainingJobs, parseMusicLoraTrainingRequest, parseYueLoraPairSettings } from './generated'
import type { MusicLoraTrainingStatuses, MusicLoraTrainingJob, MusicLoraTrainingJobs, MusicLoraTrainingRequest, YueLoraPairSettings } from './generated'
const base = '/api/music-lora/training'
function jobPath(id: string): string {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid music training job ID')
  return `${base}/jobs/${id}`
}
export function musicTrainingStatus(signal?: AbortSignal): Promise<MusicLoraTrainingStatuses> { return apiFetch(`${base}/status`, { signal }, parseMusicLoraTrainingStatuses) }
export function listMusicTraining(signal?: AbortSignal): Promise<MusicLoraTrainingJobs> { return apiFetch(`${base}/jobs`, { signal }, parseMusicLoraTrainingJobs) }
export function submitMusicTraining(request: MusicLoraTrainingRequest, signal?: AbortSignal): Promise<MusicLoraTrainingJob> {
  return apiFetch(`${base}/jobs`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseMusicLoraTrainingRequest(request)) }, parseMusicLoraTrainingJob)
}
export function createMusicTrainingPackage(request: MusicLoraTrainingRequest, signal?: AbortSignal): Promise<MusicLoraTrainingJob> {
  return apiFetch(`${base}/packages`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseMusicLoraTrainingRequest(request)) }, parseMusicLoraTrainingJob)
}
export function musicTrainingPackageUrl(id: string): string { return `${jobPath(id)}/package` }
export function cancelMusicTraining(id: string, signal?: AbortSignal): Promise<MusicLoraTrainingJob> { return apiFetch(`${jobPath(id)}/cancel`, { method: 'POST', signal }, parseMusicLoraTrainingJob) }
export function trainMissingMusicPlanner(id: string, settings: YueLoraPairSettings, signal?: AbortSignal): Promise<MusicLoraTrainingJob> {
  return apiFetch(`${jobPath(id)}/planner`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseYueLoraPairSettings(settings)) }, parseMusicLoraTrainingJob)
}
