import { apiFetch } from './http'
import { parseAudiobookBook, parseSetAudiobookPacingRequest, parseNarrationDurationRequest, parseNarrationDurationGuidance } from './contracts'
import type { SetAudiobookPacingRequest, NarrationDurationRequest } from './contracts'

export function updatePacing(bookId: string, body: SetAudiobookPacingRequest, signal?: AbortSignal) {
  if (!/^[0-9a-f]{32}$/.test(bookId)) throw new TypeError('Invalid audiobook identifier')
  const validated = parseSetAudiobookPacingRequest(body)
  return apiFetch(`/api/audiobooks/${bookId}/pacing`, { method: 'PUT', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(validated) }, parseAudiobookBook)
}
export function durationGuidance(body: NarrationDurationRequest, signal?: AbortSignal) {
  const validated = parseNarrationDurationRequest(body)
  return apiFetch('/api/audiobooks/duration-guidance', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(validated) }, parseNarrationDurationGuidance)
}
