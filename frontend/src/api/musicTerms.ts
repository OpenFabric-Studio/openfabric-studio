import { apiFetch } from './http'
import { parseMusicTermsAcceptRequest, parseMusicTermsSettingsRequest, parseMusicTermsSettingsResponse, parseMusicTermsSnapshot } from './generated'
import type { MusicTermsAcceptRequest, MusicTermsSettingsRequest, MusicTermsSnapshot } from './generated'

export async function getMusicTerms(engine: MusicTermsSnapshot['engine'], operation: MusicTermsSnapshot['operation'], signal?: AbortSignal): Promise<MusicTermsSnapshot> {
  const query = new URLSearchParams({ engine, operation })
  const result = await apiFetch(`/api/music-terms?${query}`, { signal }, parseMusicTermsSnapshot)
  if (result.engine !== engine || result.operation !== operation) throw new Error('Music terms response does not match the requested workflow')
  return result
}

export function acceptMusicTerms(body: MusicTermsAcceptRequest, signal?: AbortSignal): Promise<MusicTermsSnapshot> {
  const request = parseMusicTermsAcceptRequest(body)
  return apiFetch('/api/music-terms/accept', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) }, parseMusicTermsSnapshot)
}

export function updateMusicTermsSettings(body: MusicTermsSettingsRequest, signal?: AbortSignal) {
  const request = parseMusicTermsSettingsRequest(body)
  return apiFetch('/api/music-terms/settings', { method: 'PATCH', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) }, parseMusicTermsSettingsResponse)
}
