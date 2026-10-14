import { apiFetch } from './http'
import {
  parseArtistsResponse, parseCreateArtistRequest, parseArtistProfile, parseArtistConsentsResponse,
  parseArtistConsentRequest, parseArtistConsentVersion, parseSourceAssetsResponse, parseSourceDeclaration,
  parseSourceAsset, parseRightsReviewRequest, parseProductionRunsResponse, parseCleanRenderRequest,
  parseProductionRun, parseReleaseReviewRequest, parseModelUsageLedger, parseVoiceDeletionPlan,
  parseConfirmVoiceDeletionRequest, parseVoiceDeletionReceipt,
  parseAmendSourceRequest,
} from './contracts'
import type { ArtistConsentRequest, CreateArtistRequest, SourceDeclaration, RightsReviewRequest, CleanRenderRequest, ReleaseReviewRequest, ConfirmVoiceDeletionRequest } from './contracts'

function identifier(value: string): string {
  if (!/^[0-9a-f]{32}$/.test(value)) throw new Error('Invalid artist production identifier')
  return value
}
function json(body: unknown, signal?: AbortSignal): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }
}
export async function listArtists(signal?: AbortSignal) {
  return (await apiFetch('/api/artists', { signal }, parseArtistsResponse)).artists ?? []
}
export async function createArtist(body: CreateArtistRequest, signal?: AbortSignal) {
  return apiFetch('/api/artists', json(parseCreateArtistRequest(body), signal), parseArtistProfile)
}
export async function listArtistConsents(artistId: string, signal?: AbortSignal) {
  return (await apiFetch(`/api/artists/${identifier(artistId)}/consents`, { signal }, parseArtistConsentsResponse)).versions ?? []
}
export async function appendArtistConsent(artistId: string, body: ArtistConsentRequest, signal?: AbortSignal) {
  return apiFetch(`/api/artists/${identifier(artistId)}/consents`, json(parseArtistConsentRequest(body), signal), parseArtistConsentVersion)
}
export async function listSourceAssets(signal?: AbortSignal) {
  return (await apiFetch('/api/artist-production/assets', { signal }, parseSourceAssetsResponse)).assets
}
export async function uploadSourceAsset(audio: File, declaration: SourceDeclaration, signal?: AbortSignal) {
  const form = new FormData()
  form.append('audio', audio, audio.name)
  form.append('declaration', JSON.stringify(parseSourceDeclaration(declaration)))
  return apiFetch('/api/artist-production/assets', { method: 'POST', body: form, signal }, parseSourceAsset)
}
export async function reviewSourceAsset(assetId: string, body: RightsReviewRequest, signal?: AbortSignal) {
  return apiFetch(`/api/artist-production/assets/${identifier(assetId)}/reviews`, json(parseRightsReviewRequest(body), signal), parseSourceAsset)
}
export async function amendSourceEvidence(assetId: string, body: import('./contracts').AmendSourceRequest, signal?: AbortSignal) {
  return apiFetch(`/api/artist-production/assets/${identifier(assetId)}/declarations`, json(parseAmendSourceRequest(body), signal), parseSourceAsset)
}
export async function listProductionRuns(signal?: AbortSignal) {
  return (await apiFetch('/api/artist-production/runs', { signal }, parseProductionRunsResponse)).runs
}
export async function startCleanRender(body: CleanRenderRequest, signal?: AbortSignal) {
  return apiFetch('/api/artist-production/runs', json(parseCleanRenderRequest(body), signal), parseProductionRun)
}
export async function reviewRelease(runId: string, body: ReleaseReviewRequest, signal?: AbortSignal) {
  return apiFetch(`/api/artist-production/runs/${identifier(runId)}/release-review`, json(parseReleaseReviewRequest(body), signal), parseProductionRun)
}
export async function getUsageLedger(signal?: AbortSignal) {
  return apiFetch('/api/artist-production/usage-ledger', { signal }, parseModelUsageLedger)
}
export async function planVoiceDeletion(voiceId: string, signal?: AbortSignal) {
  return apiFetch(`/api/voices/${identifier(voiceId)}/retention`, { method: 'POST', signal }, parseVoiceDeletionPlan)
}
export async function getVoiceDeletionReceipt(planId: string, signal?: AbortSignal) {
  return apiFetch(`/api/voice-retention/${identifier(planId)}/receipt`, { signal }, parseVoiceDeletionReceipt)
}
export async function confirmVoiceDeletion(planId: string, body: ConfirmVoiceDeletionRequest, signal?: AbortSignal) {
  return apiFetch(`/api/voice-retention/${identifier(planId)}/confirm`, json(parseConfirmVoiceDeletionRequest(body), signal), parseVoiceDeletionReceipt)
}
