import { apiFetch } from './http'
import { parseAudioVersion, parseCreateAudioVersionRequest, parseTrackAudioVersionsResponse } from './contracts'
export type { AudioVersion, TrackAudioVersionsResponse } from './contracts'

function base(trackId: number) {
  if (!Number.isSafeInteger(trackId) || trackId < 1) throw new Error('Invalid track ID')
  return `/api/tracks/${trackId}/versions`
}
function item(trackId: number, versionId: string) {
  if (!/^[0-9a-f]{32}$/.test(versionId)) throw new Error('Invalid audio version ID')
  return `${base(trackId)}/${versionId}`
}
export async function listAudioVersions(trackId: number, signal?: AbortSignal) {
  return parseTrackAudioVersionsResponse(await apiFetch(base(trackId), { signal }))
}
export async function createAudioVersion(trackId: number, voiceId: string, signal?: AbortSignal) {
  const body = parseCreateAudioVersionRequest({ voice_id: voiceId })
  return parseAudioVersion(await apiFetch(base(trackId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }))
}
async function action(trackId: number, versionId: string, action: 'cancel' | 'retry', signal?: AbortSignal) {
  return parseAudioVersion(await apiFetch(`${item(trackId, versionId)}/${action}`, { method: 'POST', signal }))
}
export const cancelAudioVersion = (trackId: number, versionId: string, signal?: AbortSignal) => action(trackId, versionId, 'cancel', signal)
export const retryAudioVersion = (trackId: number, versionId: string, signal?: AbortSignal) => action(trackId, versionId, 'retry', signal)
