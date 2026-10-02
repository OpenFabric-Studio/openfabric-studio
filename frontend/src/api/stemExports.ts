import { apiFetch } from './http'
import { parseStemAudioExportResponse, parseStemAudioExportsResponse } from './contracts'
import type { StemAudioExportResponse } from './contracts'
function base(trackId: number, stem: string): string {
  if (!Number.isSafeInteger(trackId) || trackId < 1 || !['vocals', 'drums', 'bass', 'other'].includes(stem)) throw new TypeError('Invalid stem identity')
  return `/api/tracks/${trackId}/stems/${stem}/exports`
}
function item(trackId: number, stem: string, id: string): string {
  if (!/^[0-9a-f]{32}$/.test(id)) throw new TypeError('Invalid stem export identity')
  return `${base(trackId, stem)}/${id}`
}
export async function list(trackId: number, stem: string, signal?: AbortSignal): Promise<StemAudioExportResponse[]> {
  return (await apiFetch(base(trackId, stem), { signal }, parseStemAudioExportsResponse)).exports ?? []
}
export async function create(trackId: number, stem: string, signal?: AbortSignal): Promise<StemAudioExportResponse> {
  return apiFetch(base(trackId, stem), { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: '{"format":"mp3"}' }, parseStemAudioExportResponse)
}
export function get(trackId: number, stem: string, id: string, signal?: AbortSignal): Promise<StemAudioExportResponse> {
  return apiFetch(item(trackId, stem, id), { signal }, parseStemAudioExportResponse)
}
export function cancel(trackId: number, stem: string, id: string, signal?: AbortSignal): Promise<StemAudioExportResponse> {
  return apiFetch(`${item(trackId, stem, id)}/cancel`, { method: 'POST', signal }, parseStemAudioExportResponse)
}
export function retry(trackId: number, stem: string, id: string, signal?: AbortSignal): Promise<StemAudioExportResponse> {
  return apiFetch(`${item(trackId, stem, id)}/retry`, { method: 'POST', signal }, parseStemAudioExportResponse)
}
