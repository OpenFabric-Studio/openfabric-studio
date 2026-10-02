import { apiFetch, type Decoder } from './http'
import {
  parseReferenceCapabilities, parseReferenceProbeRequest, parseReferenceSource, parseReferenceImportRequest,
  parseReferenceTrackPreparationRequest, parseReferenceImport, parseReferenceImportsResponse, parseReferenceDeleteResponse,
  parseReferenceAbcRequest, parseReferenceAbcResponse, parseReferenceLyricsRequest, parseReferenceLyrics,
} from './contracts'
import type { ReferenceImportRequest, ReferenceTrackPreparationRequest, ReferenceAbcRequest, ReferenceLyricsRequest } from './contracts'
const base = '/api/references'
function id(value: string) { if (!/^[a-f0-9]{32}$/.test(value)) throw new TypeError('Invalid reference ID'); return value }
function post<T>(path: string, body: unknown, decode: Decoder<T>, signal?: AbortSignal) {
  return apiFetch(`${base}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }, decode)
}
export function capabilities(signal?: AbortSignal) { return apiFetch(`${base}/capabilities`, { signal }, parseReferenceCapabilities) }
export function probe(url: string, signal?: AbortSignal) { return post('/probe', parseReferenceProbeRequest({ url }), parseReferenceSource, signal) }
export function list(signal?: AbortSignal) { return apiFetch(`${base}/imports`, { signal }, parseReferenceImportsResponse).then(response => response.imports) }
export function get(importId: string, signal?: AbortSignal) { return apiFetch(`${base}/imports/${id(importId)}`, { signal }, parseReferenceImport) }
export async function submit(request: ReferenceImportRequest, signal?: AbortSignal) {
  const body = parseReferenceImportRequest(request)
  if (body.lyrics_source === 'subtitles' && !body.subtitle_language?.trim()) throw new TypeError('Subtitle language is required')
  return post('/imports', body, parseReferenceImport, signal)
}
export async function prepare(request: ReferenceTrackPreparationRequest, signal?: AbortSignal) { return post('/prepare', parseReferenceTrackPreparationRequest(request), parseReferenceImport, signal) }
export async function cancel(importId: string, signal?: AbortSignal) { return post(`/imports/${id(importId)}/cancel`, {}, parseReferenceImport, signal) }
export async function retry(importId: string, signal?: AbortSignal) { return post(`/imports/${id(importId)}/retry`, {}, parseReferenceImport, signal) }
export async function remove(importId: string, signal?: AbortSignal) { return apiFetch(`${base}/imports/${id(importId)}`, { method: 'DELETE', signal }, parseReferenceDeleteResponse) }
export async function transformAbc(request: ReferenceAbcRequest, signal?: AbortSignal) {
  const body = parseReferenceAbcRequest(request)
  const min = body.target_min_midi; const max = body.target_max_midi
  if ((min == null) !== (max == null) || min != null && max != null && min > max) throw new TypeError('Invalid MIDI range')
  return post('/tools/abc', body, parseReferenceAbcResponse, signal)
}
export async function transformLyrics(request: ReferenceLyricsRequest, signal?: AbortSignal) { return post('/tools/lyrics', parseReferenceLyricsRequest(request), parseReferenceLyrics, signal) }
