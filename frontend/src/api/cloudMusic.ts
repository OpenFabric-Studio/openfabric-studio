import { apiFetch } from './http'
import { parseCloudMusicJob, parseCloudMusicJobs, parseOpenRouterQuote } from './generated'
import type { CloudMusicJob, CloudMusicJobs, CloudMusicSubmitRequest, OpenRouterMusicRequest, OpenRouterQuote } from './generated'
export function quoteCloudMusic(body: OpenRouterMusicRequest, signal?: AbortSignal): Promise<OpenRouterQuote> { return apiFetch('/api/cloud-music/quote', { method: 'POST',signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body) },parseOpenRouterQuote) }
export function submitCloudMusic(body: CloudMusicSubmitRequest, signal?: AbortSignal): Promise<CloudMusicJob> { return apiFetch('/api/cloud-music/jobs',{method:'POST',signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},parseCloudMusicJob) }
export function listCloudMusic(signal?: AbortSignal): Promise<CloudMusicJobs> { return apiFetch('/api/cloud-music/jobs',{signal},parseCloudMusicJobs) }
export function cancelCloudMusic(id: string, signal?: AbortSignal): Promise<CloudMusicJob> { return apiFetch(`/api/cloud-music/jobs/${encodeURIComponent(id)}/cancel`,{method:'POST',signal},parseCloudMusicJob) }
export function retrySaveCloudMusic(id: string, signal?: AbortSignal): Promise<CloudMusicJob> { return apiFetch(`/api/cloud-music/jobs/${encodeURIComponent(id)}/retry-save`,{method:'POST',signal},parseCloudMusicJob) }
