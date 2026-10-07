import { apiFetch } from './http'
import {
  parseReadAlongExport, parseReadAlongExportsResponse, parseReadAlongRequest,
  parseRetainedAudioSource, parseRetainedAudioInfo, parseRetainedAudioVideoRequest, parseVideoProject,
} from './contracts'
import type { ReadAlongRequest, RetainedAudioSource, RetainedAudioVideoRequest } from './contracts'

function id(value: string): string {
  if (!/^[0-9a-f]{32}$/.test(value)) throw new TypeError('Invalid source identifier')
  return value
}
function chapterPath(bookId: string, chapterIndex: number): string {
  if (!Number.isInteger(chapterIndex) || chapterIndex < 0 || chapterIndex > 99) throw new TypeError('Invalid chapter index')
  return `/api/reading-media/books/${id(bookId)}/chapters/${chapterIndex}/exports`
}
function jsonRequest(body: unknown, signal?: AbortSignal): RequestInit {
  return { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}
export function createReadAlong(bookId: string, chapterIndex: number, body: ReadAlongRequest, signal?: AbortSignal) {
  return apiFetch(chapterPath(bookId, chapterIndex), jsonRequest(parseReadAlongRequest(body), signal), parseReadAlongExport)
}
export async function listReadAlong(bookId: string, chapterIndex: number, signal?: AbortSignal) {
  const result = await apiFetch(chapterPath(bookId, chapterIndex), { signal }, parseReadAlongExportsResponse)
  return result.exports
}
export function getReadAlong(exportId: string, signal?: AbortSignal) {
  return apiFetch(`/api/reading-media/exports/${id(exportId)}`, { signal }, parseReadAlongExport)
}
export function cancelReadAlong(exportId: string, signal?: AbortSignal) {
  return apiFetch(`/api/reading-media/exports/${id(exportId)}/cancel`, { method: 'POST', signal }, parseReadAlongExport)
}
export function resumeReadAlong(exportId: string, signal?: AbortSignal) {
  return apiFetch(`/api/reading-media/exports/${id(exportId)}/resume`, { method: 'POST', signal }, parseReadAlongExport)
}
export function retainedAudioInfo(source: RetainedAudioSource, signal?: AbortSignal) {
  return apiFetch('/api/reading-media/audio/info', jsonRequest(parseRetainedAudioSource(source), signal), parseRetainedAudioInfo)
}
export function retainedAudioVideo(body: RetainedAudioVideoRequest, signal?: AbortSignal) {
  return apiFetch('/api/reading-media/audio/video', jsonRequest(parseRetainedAudioVideoRequest(body), signal), parseVideoProject)
}
