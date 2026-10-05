import { apiFetch } from './http'
import {
  parseAudiobookPassagesResponse, parseAudiobookAudition, parseAudiobookAuditionOptions,
  parseCreateAudiobookAuditionRequest, parseAudiobookRepair,
  parseCreateAudiobookRepairRequest, parseAcceptAudiobookRepairRequest,
  parseAudiobookAuditionsResponse, parseAudiobookRepairsResponse,
} from './contracts'
import type { AudiobookAuditionOptions, CreateAudiobookAuditionRequest, CreateAudiobookRepairRequest } from './contracts'

function id(value: string): string {
  if (!/^[0-9a-f]{32}$/.test(value)) throw new TypeError('Invalid audiobook workflow identifier')
  return value
}
function chapterPath(bookId: string, index: number): string {
  if (!Number.isInteger(index) || index < 0 || index > 99) throw new TypeError('Invalid chapter index')
  return `/api/audiobooks/${id(bookId)}/chapters/${index}`
}
function json(body: unknown, signal?: AbortSignal): RequestInit {
  return { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}
export function listPassages(bookId: string, chapterIndex: number, signal?: AbortSignal) {
  return apiFetch(`${chapterPath(bookId, chapterIndex)}/passages`, { signal }, parseAudiobookPassagesResponse)
}
export function auditionDraft(body: CreateAudiobookAuditionRequest, signal?: AbortSignal) {
  return apiFetch('/api/audiobooks/auditions', json(parseCreateAudiobookAuditionRequest(body), signal), parseAudiobookAudition)
}
export function auditionBook(bookId: string, body: AudiobookAuditionOptions, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/${id(bookId)}/auditions`, json(parseAudiobookAuditionOptions(body), signal), parseAudiobookAudition)
}
export function getAudition(auditionId: string, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/auditions/${id(auditionId)}`, { signal }, parseAudiobookAudition)
}
export async function listBookAuditions(bookId: string, chapterIndex: number, signal?: AbortSignal) {
  chapterPath(bookId, chapterIndex)
  const result = await apiFetch(`/api/audiobooks/${id(bookId)}/auditions?chapter_index=${chapterIndex}`, { signal }, parseAudiobookAuditionsResponse)
  return result.auditions
}
export function cancelAudition(auditionId: string, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/auditions/${id(auditionId)}/cancel`, { method: 'POST', signal }, parseAudiobookAudition)
}
export function createRepair(bookId: string, chapterIndex: number, passageId: string, body: CreateAudiobookRepairRequest, signal?: AbortSignal) {
  return apiFetch(`${chapterPath(bookId, chapterIndex)}/passages/${id(passageId)}/repairs`, json(parseCreateAudiobookRepairRequest(body), signal), parseAudiobookRepair)
}
export function getRepair(repairId: string, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/repairs/${id(repairId)}`, { signal }, parseAudiobookRepair)
}
export async function listPassageRepairs(bookId: string, chapterIndex: number, passageId: string, revision: number, signal?: AbortSignal) {
  if (!Number.isInteger(revision) || revision < 1) throw new TypeError('Invalid passage revision')
  const result = await apiFetch(`${chapterPath(bookId, chapterIndex)}/passages/${id(passageId)}/repairs?revision=${revision}`, { signal }, parseAudiobookRepairsResponse)
  return result.repairs
}
export function cancelRepair(repairId: string, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/repairs/${id(repairId)}/cancel`, { method: 'POST', signal }, parseAudiobookRepair)
}
export function acceptRepair(repairId: string, revision: number, signal?: AbortSignal) {
  return apiFetch(`/api/audiobooks/repairs/${id(repairId)}/accept`, json(parseAcceptAudiobookRepairRequest({ revision }), signal), parseAudiobookPassagesResponse)
}
/** Even schema-valid strings must be restricted before becoming browser media URLs. */
export function workflowAudioUrl(value: string | null | undefined, revision?: number): string | undefined {
  if (!value) return undefined
  const match = /^\/api\/audiobooks\/(?:[0-9a-f]{32}\/passages\/[0-9a-f]{32}|auditions\/[0-9a-f]{32}(?:\/clips\/(?:[0-9]|1[0-6]))?|repairs\/[0-9a-f]{32})\/audio(?:\?revision=([1-9][0-9]*))?$/.exec(value)
  if (!match) return undefined
  const supplied = match[1] === undefined ? undefined : Number(match[1])
  if (supplied !== undefined && !Number.isSafeInteger(supplied)) return undefined
  if (revision === undefined) return value
  if (!Number.isSafeInteger(revision) || revision < 1 || supplied !== undefined && supplied !== revision) return undefined
  return supplied === undefined ? `${value}?revision=${revision}` : value
}
