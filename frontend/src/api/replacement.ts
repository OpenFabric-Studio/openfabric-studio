import { apiFetch } from './http'
import { parseAlignmentEditRequest, parseAlignmentResult, parseReplacementActionRequest, parseReplacementCapabilities, parseReplacementMixEditRequest, parseReplacementPlanResponse, parseReplacementPlansResponse, parseReplacementRenderRequest, parseReplacementStoragePreview, parseReplacementUpdateRequest } from './generated'
import type { AlignmentEditRequest, ReplacementActionRequest, ReplacementMixEditRequest, ReplacementPlanResponse, ReplacementRenderRequest, ReplacementUpdateRequest } from './generated'

export type ReplacementRequest = ReplacementUpdateRequest['request']
const base = '/api/voices/replace'
function path(id: string): string { if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid replacement plan'); return `${base}/plans/${id}` }
function json(body: object, signal?: AbortSignal, method = 'POST'): RequestInit { return { method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } }
export function replacementRequest(value: unknown): ReplacementRequest { return parseReplacementUpdateRequest({ expected_revision: 1, request: value }).request }
function owned(value: unknown, id: string): ReplacementPlanResponse {
  const result = parseReplacementPlanResponse(value)
  if (result.id !== id || result.artifacts?.some(item => item.audio_url && item.audio_url !== `${path(id)}/artifacts/${item.id}/audio`)) throw new TypeError('Mismatched replacement plan')
  const report = result.diagnostics
  if (report) {
    const missing = report.unavailable ?? [], artifacts = new Map(result.artifacts?.map(item => [item.id, item]))
    if (report.revision !== (result.revision ?? 1) || report.tonal && (report.tonal.plan_id !== id || report.tonal.revision !== report.revision)
      || new Set(missing).size !== missing.length || missing.includes('pitch') !== !report.pitch || missing.includes('tonal') !== !report.tonal) throw new TypeError('Mismatched replacement measurements')
    for (const { identity } of [...report.pitch?.artifacts ?? [], ...report.tonal?.artifacts ?? []]) {
      const artifact = artifacts.get(identity.artifact_id)
      if (!artifact || artifact.sha256 !== identity.sha256 || artifact.channels !== identity.channels
        || artifact.sample_rate_hz !== identity.sample_rate_hz || Math.round(artifact.duration_sec * artifact.sample_rate_hz) !== identity.duration_frames) throw new TypeError('Mismatched measurement artifact')
    }
  }
  return result
}
export const replacementCapabilities = (signal?: AbortSignal) => apiFetch(`${base}/capabilities`, { signal }, parseReplacementCapabilities)
export const previewReplacementStorage = (request: ReplacementRequest, signal?: AbortSignal) => apiFetch(`${base}/storage-preview`, json(replacementRequest(request), signal), parseReplacementStoragePreview)
export function createReplacementPlan(request: ReplacementRequest, signal?: AbortSignal, requestId?: string) {
  if (requestId) path(requestId)
  return apiFetch(`${base}/plans${requestId ? `?request_id=${requestId}` : ''}`, json(replacementRequest(request), signal), value => {
    const result = parseReplacementPlanResponse(value)
    return owned(value, requestId ?? result.id)
  })
}
export const getReplacementPlan = (id: string, signal?: AbortSignal) => apiFetch(path(id), { signal }, value => owned(value, id))
export function listReplacementPlans(cursor?: string, signal?: AbortSignal) {
  if (cursor) path(cursor)
  return apiFetch(`${base}/plans${cursor ? `?cursor=${cursor}` : ''}`, { signal }, parseReplacementPlansResponse)
}
export const updateReplacementPlan = (id: string, request: ReplacementUpdateRequest, signal?: AbortSignal) => apiFetch(path(id), json(parseReplacementUpdateRequest(request), signal, 'PATCH'), value => owned(value, id))
export const editReplacementMix = (id: string, request: ReplacementMixEditRequest, signal?: AbortSignal) => apiFetch(`${path(id)}/mix`, json(parseReplacementMixEditRequest(request), signal, 'PATCH'), value => owned(value, id))
export const getReplacementAlignment = (id: string, signal?: AbortSignal) => apiFetch(`${path(id)}/alignment`, { signal }, value => value === null ? null : parseAlignmentResult(value))
export const editReplacementAlignment = (id: string, request: AlignmentEditRequest, signal?: AbortSignal) => apiFetch(`${path(id)}/alignment`, json(parseAlignmentEditRequest(request), signal, 'PATCH'), value => owned(value, id))
export function prepareReplacementPlan(id: string, request: ReplacementActionRequest, signal?: AbortSignal) { return action(id, 'prepare', request, signal) }
export function retryReplacementPlan(id: string, request: ReplacementActionRequest, signal?: AbortSignal) { return action(id, 'retry', request, signal) }
function action(id: string, action: 'prepare' | 'retry', request: ReplacementActionRequest, signal?: AbortSignal) { return apiFetch(`${path(id)}/${action}`, json(parseReplacementActionRequest(request), signal), value => owned(value, id)) }
export function renderReplacement(id: string, mode: 'preview' | 'render' | 'remix', request: ReplacementRenderRequest, signal?: AbortSignal) { return apiFetch(`${path(id)}/${mode}`, json(parseReplacementRenderRequest(request), signal), value => owned(value, id)) }
export const cancelReplacementPlan = (id: string, signal?: AbortSignal) => apiFetch(`${path(id)}/cancel`, { method: 'POST', signal }, value => owned(value, id))
export const deleteReplacementPlan = (id: string, signal?: AbortSignal) => apiFetch(path(id), { method: 'DELETE', signal })
