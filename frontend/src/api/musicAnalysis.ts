import { apiFetch } from './http'
import { parseAnalysisResult, parseMusicAnalysisJob, parseMusicAnalysisOverrides, parseMusicAnalysisRequest, parseMusicAnalysisStoragePreview } from './generated'
import type { MusicAnalysisJob, MusicAnalysisOverrides, MusicAnalysisRequest } from './generated'

export type MusicSource = MusicAnalysisRequest['source']
const base = '/api/music/analysis'
function path(id: string) { if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid music analysis identifier'); return `${base}/${id}` }
export function musicSourceKey(source: MusicSource): string {
  if (source.kind === 'version') return `version:${source.track_id}:${source.version_id}`
  if (source.kind === 'stem') return `stem:${source.track_id}:${source.stem}:${source.sha256}`
  return `asset:${source.asset_id}:${source.sha256}`
}
function ownedJob(value: unknown, id: string): MusicAnalysisJob {
  const job = parseMusicAnalysisJob(value)
  if (job.id !== id) throw new TypeError('Mismatched music analysis job')
  return job
}
export function listMusicAnalysis(source?: MusicSource, signal?: AbortSignal): Promise<MusicAnalysisJob[]> {
  const requested = source ? parseMusicAnalysisRequest({ source }).source : undefined
  const query = requested ? `?${new URLSearchParams({ source: JSON.stringify(requested) })}` : ''
  return apiFetch(`${base}${query}`, { signal }, (value: unknown) => {
    if (!Array.isArray(value) || value.length > 100) throw new TypeError('Invalid music analysis list')
    const jobs = value.map((item: unknown) => parseMusicAnalysisJob(item))
    if (requested && jobs.some(job => musicSourceKey(job.source) !== musicSourceKey(requested))) throw new TypeError('Mismatched music analysis history source')
    return jobs
  })
}
export function getMusicAnalysis(id: string, signal?: AbortSignal): Promise<MusicAnalysisJob> {
  return apiFetch(path(id), { signal }, value => ownedJob(value, id))
}
export function startMusicAnalysis(body: MusicAnalysisRequest, signal?: AbortSignal): Promise<MusicAnalysisJob> {
  const request = parseMusicAnalysisRequest(body)
  return apiFetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal }, value => {
    const job = parseMusicAnalysisJob(value)
    if (musicSourceKey(job.source) !== musicSourceKey(request.source)) throw new TypeError('Mismatched music analysis source')
    return job
  })
}
export function previewMusicAnalysis(body: MusicAnalysisRequest, signal?: AbortSignal) {
  const request = parseMusicAnalysisRequest(body)
  return apiFetch(`${base}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal }, parseMusicAnalysisStoragePreview)
}
function action(id: string, action: 'cancel' | 'retry', signal?: AbortSignal): Promise<MusicAnalysisJob> {
  return apiFetch(`${path(id)}/${action}`, { method: 'POST', signal }, value => ownedJob(value, id))
}
export const cancelMusicAnalysis = (id: string, signal?: AbortSignal) => action(id, 'cancel', signal)
export const retryMusicAnalysis = (id: string, signal?: AbortSignal) => action(id, 'retry', signal)
export function saveMusicAnalysisOverrides(body: MusicAnalysisOverrides, signal?: AbortSignal) {
  const request = parseMusicAnalysisOverrides(body)
  return apiFetch(`${path(request.analysis_id)}/overrides`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal }, value => {
    const result = parseAnalysisResult(value)
    if (result.id !== request.analysis_id) throw new TypeError('Mismatched music analysis result')
    return result
  })
}
