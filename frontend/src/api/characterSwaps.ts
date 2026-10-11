import { apiFetch } from './http'
import { parseCharacterSwapJob, parseCharacterSwapReadiness, parseCharacterSwapsResponse,
  parseSubmitCharacterSwapRequest, parseReviewCharacterSwapRequest, parseCharacterSwapRevisionRequest, parseRenameCharacterSwapRequest, parseDeletedCharacterSwapResponse } from './contracts'
import type { CharacterSwapJob, CharacterSwapReadiness, CharacterSwapsResponse,
  SubmitCharacterSwapRequest, ReviewCharacterSwapRequest, CharacterSwapRevisionRequest, RenameCharacterSwapRequest, DeletedCharacterSwapResponse } from './contracts'

const path = '/api/videos/character-swaps'
export const CHARACTER_SWAP_MAX_VIDEO_BYTES = 100 * 1024 * 1024
function jobPath(id: string) { return `${path}/${encodeURIComponent(id)}` }

export function characterSwapReadiness(signal?: AbortSignal): Promise<CharacterSwapReadiness> {
  return apiFetch(`${path}/readiness`, { signal }, parseCharacterSwapReadiness)
}
export function listCharacterSwaps(signal?: AbortSignal): Promise<CharacterSwapsResponse> {
  return apiFetch(path, { signal }, parseCharacterSwapsResponse)
}
export function getCharacterSwap(id: string, signal?: AbortSignal): Promise<CharacterSwapJob> {
  return apiFetch(jobPath(id), { signal }, parseCharacterSwapJob)
}
export function renameCharacterSwap(id: string, body: RenameCharacterSwapRequest, signal?: AbortSignal): Promise<CharacterSwapJob> {
  return apiFetch(jobPath(id), { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseRenameCharacterSwapRequest(body)), signal }, parseCharacterSwapJob)
}
export function deleteCharacterSwap(id: string, body: CharacterSwapRevisionRequest, signal?: AbortSignal): Promise<DeletedCharacterSwapResponse> {
  return apiFetch(jobPath(id), { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseCharacterSwapRevisionRequest(body)), signal }, parseDeletedCharacterSwapResponse)
}
export function prepareCharacterSwap(input: { video: File; image: File; name: string; startSec: number; seconds: number }, signal?: AbortSignal): Promise<CharacterSwapJob> {
  const form = new FormData()
  form.append('video', input.video, input.video.name)
  form.append('image', input.image, input.image.name)
  form.append('name', input.name)
  form.append('start_sec', String(input.startSec))
  form.append('seconds', String(input.seconds))
  return apiFetch(path, { method: 'POST', body: form, signal }, parseCharacterSwapJob)
}
export function prepareCharacterSwapFromScene(input: { sceneId: string; cutId: string; image: File; name: string; startSec: number; seconds: number }, signal?: AbortSignal): Promise<CharacterSwapJob> {
  const form = new FormData()
  form.append('scene_id', input.sceneId)
  form.append('cut_id', input.cutId)
  form.append('image', input.image, input.image.name)
  form.append('name', input.name)
  form.append('start_sec', String(input.startSec))
  form.append('seconds', String(input.seconds))
  form.append('seed', '42')
  return apiFetch(`${path}/from-scene`, { method: 'POST', body: form, signal }, parseCharacterSwapJob)
}
export function submitCharacterSwap(id: string, body: SubmitCharacterSwapRequest, signal?: AbortSignal): Promise<CharacterSwapJob> {
  return apiFetch(`${jobPath(id)}/submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseSubmitCharacterSwapRequest(body)), signal }, parseCharacterSwapJob)
}
export function quoteCharacterSwap(id: string, body: CharacterSwapRevisionRequest, signal?: AbortSignal): Promise<CharacterSwapJob> {
  return apiFetch(`${jobPath(id)}/quote`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseCharacterSwapRevisionRequest(body)), signal }, parseCharacterSwapJob)
}
export function reviewCharacterSwap(id: string, body: ReviewCharacterSwapRequest, signal?: AbortSignal): Promise<CharacterSwapJob> {
  return apiFetch(`${jobPath(id)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseReviewCharacterSwapRequest(body)), signal }, parseCharacterSwapJob)
}
export function resumeCharacterSwap(id: string, body: CharacterSwapRevisionRequest, signal?: AbortSignal): Promise<CharacterSwapJob> {
  return apiFetch(`${jobPath(id)}/resume`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseCharacterSwapRevisionRequest(body)), signal }, parseCharacterSwapJob)
}
export function stopCharacterSwap(id: string, body: CharacterSwapRevisionRequest, signal?: AbortSignal): Promise<CharacterSwapJob> {
  return apiFetch(`${jobPath(id)}/stop`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseCharacterSwapRevisionRequest(body)), signal }, parseCharacterSwapJob)
}
