import { apiFetch } from './http'
import { parseSingerRangeProfile, parseSingerRangeRequest, parsePlannedRangeRequest, parseRangeAssessment } from './contracts'
import type { SingerRangeProfile, SingerRangeRequest, PlannedRangeRequest } from './contracts'

function rangeUrl(artistId: string): string {
  if (!/^[0-9a-f]{32}$/.test(artistId)) throw new Error('Invalid artist range identifier')
  return `/api/artists/${artistId}/range`
}
function post(body: unknown, signal?: AbortSignal): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }
}
function profileFor(artistId: string, value: unknown): SingerRangeProfile {
  const profile = parseSingerRangeProfile(value)
  if (profile.artist_id !== artistId) throw new TypeError('Invalid artist range identity')
  return profile
}
export async function getSingerRange(artistId: string, signal?: AbortSignal): Promise<SingerRangeProfile | null> {
  return apiFetch(rangeUrl(artistId), { signal }, (value: unknown) => value === null ? null : profileFor(artistId, value))
}
export async function saveSingerRange(artistId: string, body: SingerRangeRequest, signal?: AbortSignal) {
  return apiFetch(rangeUrl(artistId), post(parseSingerRangeRequest(body), signal), (value: unknown) => profileFor(artistId, value))
}
export async function assessSingerRange(artistId: string, body: PlannedRangeRequest, signal?: AbortSignal) {
  return apiFetch(`${rangeUrl(artistId)}/assess`, post(parsePlannedRangeRequest(body), signal), (value: unknown) => {
    const assessment = parseRangeAssessment(value)
    if (assessment.artist_id !== artistId) throw new TypeError('Invalid artist range identity')
    return assessment
  })
}
