import { apiFetch } from './http'
import { parseBeatArchiveResponse, parseBeatCreateRequest, parseBeatDocument, parseBeatDocuments, parseBeatHumanizeRequest, parseBeatJob, parseBeatJobRequest, parseBeatJobs, parseBeatKits, parseBeatRestoreRequest, parseBeatRevisions, parseBeatStoragePreview, parseBeatUpdateRequest, parseBeatVariationRequest } from './generated'
import type { BeatCreateRequest, BeatHumanizeRequest, BeatJobRequest, BeatRestoreRequest, BeatUpdateRequest, BeatVariationRequest } from './generated'
import { parseBeatPresetCatalog, parseBeatPresetCompilation, parseBeatPresetCompileRequest, parseMusicProjectPublication } from './generated'
import type { BeatPresetCompileRequest } from './generated'

const base = '/api/beats'
function id(value: string): string { if (!/^[a-f0-9]{32}$/.test(value)) throw new TypeError('Invalid beat identifier'); return value }
const path = (value: string) => `${base}/${id(value)}`
function revision(value: number): number { if (!Number.isSafeInteger(value) || value < 0) throw new TypeError('Invalid beat revision'); return value }
function document(value: unknown, beatId: string) { const result = parseBeatDocument(value); if (result.id !== beatId) throw new TypeError('Mismatched beat document'); return result }
const json = (body: object, signal?: AbortSignal, method = 'POST'): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
export const listBeats = (signal?: AbortSignal) => apiFetch(base, { signal }, parseBeatDocuments)
export const listBeatKits = (signal?: AbortSignal) => apiFetch(`${base}/kits`, { signal }, parseBeatKits)
export const listBeatPresets = (signal?: AbortSignal) => apiFetch(`${base}/presets`, { signal }, parseBeatPresetCatalog)
export const compileBeatPreset = (body: BeatPresetCompileRequest, signal?: AbortSignal) => {
  const request = parseBeatPresetCompileRequest(body)
  return apiFetch(`${base}/presets/compile`, json(request, signal), value => {
    const result = parseBeatPresetCompilation(value)
    if (result.preset_id !== request.preset_id || result.document.id !== request.document_id || result.document.kit_id !== request.kit_id) throw new TypeError('Mismatched beat preset')
    return result
  })
}
export const createBeat = (body: BeatCreateRequest, signal?: AbortSignal) => apiFetch(base, json(parseBeatCreateRequest(body), signal), parseBeatDocument)
export const getBeat = (beatId: string, signal?: AbortSignal) => apiFetch(path(beatId), { signal }, value => document(value, beatId))
export const getBeatRevision = (beatId: string, version: number, signal?: AbortSignal) => apiFetch(`${path(beatId)}/revisions/${revision(version)}`, { signal }, value => {
  const result = document(value, beatId)
  if (result.revision !== version) throw new TypeError('Mismatched beat revision')
  return result
})
export const publishBeatProject = (jobId: string, beatId: string, version: number, signal?: AbortSignal) => {
  id(beatId); revision(version)
  return apiFetch(`${base}/renders/${id(jobId)}/project`, json({}, signal), value => {
    const result = parseMusicProjectPublication(value)
    if (result.provenance.workflow !== 'beat' || result.provenance.workflow_id !== beatId || result.provenance.workflow_revision !== version) throw new TypeError('Mismatched beat project')
    return result
  })
}
export function saveBeat(beatId: string, body: BeatUpdateRequest, signal?: AbortSignal) {
  const request = parseBeatUpdateRequest(body)
  if (request.document.id !== beatId || request.document.revision !== request.expected_revision) throw new TypeError('Mismatched beat revision')
  return apiFetch(path(beatId), json(request, signal, 'PATCH'), value => document(value, beatId))
}
export const listBeatRevisions = (beatId: string, signal?: AbortSignal) => apiFetch(`${path(beatId)}/revisions`, { signal }, parseBeatRevisions)
export const restoreBeat = (beatId: string, body: BeatRestoreRequest, signal?: AbortSignal) => apiFetch(`${path(beatId)}/restore`, json(parseBeatRestoreRequest(body), signal), value => document(value, beatId))
export const varyBeat = (beatId: string, body: BeatVariationRequest, signal?: AbortSignal) => apiFetch(`${path(beatId)}/variation`, json(parseBeatVariationRequest(body), signal), value => document(value, beatId))
export const humanizeBeat = (beatId: string, body: BeatHumanizeRequest, signal?: AbortSignal) => apiFetch(`${path(beatId)}/humanize`, json(parseBeatHumanizeRequest(body), signal), value => document(value, beatId))
export const archiveBeat = (beatId: string, expected: number, signal?: AbortSignal) => apiFetch(`${path(beatId)}?expected_revision=${revision(expected)}`, { method: 'DELETE', signal }, value => {
  const result = parseBeatArchiveResponse(value)
  if (result.beat_id !== beatId) throw new TypeError('Mismatched archived beat')
  return result
})
export function previewBeatStorage(beatId: string, body: BeatJobRequest, signal?: AbortSignal) {
  const request = parseBeatJobRequest(body)
  if (request.beat_id !== beatId) throw new TypeError('Mismatched render source')
  return apiFetch(`${path(beatId)}/storage-preview`, json(request, signal), parseBeatStoragePreview)
}
export function renderBeat(beatId: string, body: BeatJobRequest, signal?: AbortSignal) {
  const request = parseBeatJobRequest(body)
  if (request.beat_id !== beatId) throw new TypeError('Mismatched render source')
  return apiFetch(`${path(beatId)}/renders`, json(request, signal), value => {
    const result = parseBeatJob(value)
    if (result.beat_id !== beatId || result.beat_revision !== request.expected_revision || result.request.submission_key !== request.submission_key) throw new TypeError('Mismatched beat render')
    return result
  })
}
export const listBeatJobs = (beatId: string, signal?: AbortSignal) => apiFetch(`${base}/renders?beat_id=${id(beatId)}`, { signal }, value => {
  const result = parseBeatJobs(value)
  if (result.jobs.some(job => job.beat_id !== beatId)) throw new TypeError('Mismatched beat render history')
  return result
})
export const getBeatJob = (jobId: string, signal?: AbortSignal) => apiFetch(`${base}/renders/${id(jobId)}`, { signal }, value => {
  const job = parseBeatJob(value); if (job.id !== jobId) throw new TypeError('Mismatched beat render'); return job
})
function jobAction(jobId: string, action: 'cancel' | 'retry-save', signal?: AbortSignal) {
  return apiFetch(`${base}/renders/${id(jobId)}/${action}`, { method: 'POST', signal }, value => {
    const job = parseBeatJob(value); if (job.id !== jobId) throw new TypeError('Mismatched beat render'); return job
  })
}
export const cancelBeatJob = (jobId: string, signal?: AbortSignal) => jobAction(jobId, 'cancel', signal)
export const retryBeatSave = (jobId: string, signal?: AbortSignal) => jobAction(jobId, 'retry-save', signal)
export const beatArtifactUrl = (jobId: string, artifactId: string) => `${base}/renders/${id(jobId)}/artifacts/${id(artifactId)}`
export function importBeatKit(manifest: File, archive: File, signal?: AbortSignal) {
  const body = new FormData(); body.append('manifest', manifest); body.append('archive', archive)
  return apiFetch(`${base}/kits/import`, { method: 'POST', body, signal }, parseBeatKits)
}
