import { apiFetch } from './http'
import { parseAudioExportResponse, parseAudioExportsResponse, parseCreateAudioExportRequest } from './contracts'
export type { AudioExportResponse } from './contracts'
import type { CreateAudioExportRequest } from './contracts'
export type AudioExportFormat = CreateAudioExportRequest['format']
function base(trackId: number, versionId: string) {
  if (!Number.isSafeInteger(trackId) || trackId < 1 || !/^[0-9a-f]{32}$/.test(versionId)) throw new Error('Invalid audio version')
  return `/api/tracks/${trackId}/versions/${versionId}/exports`
}
export async function listAudioExports(trackId: number, versionId: string, signal?: AbortSignal) {
  return parseAudioExportsResponse(await apiFetch(base(trackId, versionId), { signal }))
}
export async function createAudioExport(trackId: number, versionId: string, format: AudioExportFormat, signal?: AbortSignal) {
  const body = parseCreateAudioExportRequest({ format })
  return parseAudioExportResponse(await apiFetch(base(trackId, versionId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }))
}
async function action(trackId: number, versionId: string, exportId: string, kind: 'cancel' | 'retry', signal?: AbortSignal) {
  if (!/^[0-9a-f]{32}$/.test(exportId)) throw new Error('Invalid audio export')
  return parseAudioExportResponse(await apiFetch(`${base(trackId, versionId)}/${exportId}/${kind}`, { method: 'POST', signal }))
}
export const cancelAudioExport = (trackId: number, versionId: string, exportId: string, signal?: AbortSignal) => action(trackId, versionId, exportId, 'cancel', signal)
export const retryAudioExport = (trackId: number, versionId: string, exportId: string, signal?: AbortSignal) => action(trackId, versionId, exportId, 'retry', signal)
