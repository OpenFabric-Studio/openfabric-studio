import { apiFetch } from './http'
import {
  parseAsrCapability, parseAsrReview, parseAsrReviewsResponse,
  parseAsrReviewRequest, parsePauseAnalysisSettings,
} from './contracts'
import type { AsrReviewRequest, PauseAnalysisSettings } from './contracts'

function chapterPath(bookId: string, chapterIndex: number): string {
  if (!/^[0-9a-f]{32}$/.test(bookId) || !Number.isInteger(chapterIndex) || chapterIndex < 0 || chapterIndex > 99) throw new TypeError('Invalid chapter')
  return `/api/audiobooks/${bookId}/chapters/${chapterIndex}`
}
function id(value: string): string {
  if (!/^[0-9a-f]{32}$/.test(value)) throw new TypeError('Invalid review identifier')
  return value
}
export function getPauseSettings(signal?: AbortSignal) {
  return apiFetch('/api/audiobooks/analysis/settings', { signal }, parsePauseAnalysisSettings)
}
export function savePauseSettings(body: PauseAnalysisSettings, signal?: AbortSignal) {
  return apiFetch('/api/audiobooks/analysis/settings', { method: 'PUT', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parsePauseAnalysisSettings(body)) }, parsePauseAnalysisSettings)
}
export function getAsrCapability(signal?: AbortSignal) {
  return apiFetch('/api/audiobooks/qa/capability', { signal }, parseAsrCapability)
}
export function checkPassage(bookId: string, chapterIndex: number, passageId: string, body: AsrReviewRequest, signal?: AbortSignal) {
  return apiFetch(`${chapterPath(bookId, chapterIndex)}/passages/${id(passageId)}/qa`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseAsrReviewRequest(body)) }, parseAsrReview)
}
export function getAsrReview(reviewId: string, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/qa/${id(reviewId)}`, { signal }, parseAsrReview)
}
export function cancelAsrReview(reviewId: string, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/qa/${id(reviewId)}/cancel`, { method: 'POST', signal }, parseAsrReview)
}
export async function listAsrReviews(bookId: string, signal?: AbortSignal) {
  const result = await apiFetch(`/api/audiobooks/${id(bookId)}/qa`, { signal }, parseAsrReviewsResponse)
  return result.reviews
}
