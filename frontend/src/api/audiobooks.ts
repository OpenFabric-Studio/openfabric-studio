import { apiFetch, ApiError } from './http'
import {
  parseAudiobookBook,
  parseAudiobookBooksResponse,
  parseAudiobookCreateResponse,
  parseAudiobookJobsResponse,
  parseEbookDraft, parseEbookDraftsResponse, parsePatchEbookDraftRequest,
} from './contracts'
import type {
  AudiobookBook,
  AudiobookCreateResponse,
  AudiobookJob,
  CreateAudiobookRequest,
  EbookDraft, EbookDraftSummary, PatchEbookDraftRequest,
} from './contracts'

export type { AudiobookBook, AudiobookJob, AudiobookCreateResponse, CreateAudiobookRequest }
export type { EbookDraft }

export function importEbook(file: File, signal?: AbortSignal): Promise<EbookDraft> {
  const form = new FormData(); form.append('file', file, file.name)
  return apiFetch('/api/audiobooks/imports', { method: 'POST', body: form, signal }, parseEbookDraft)
}
export function importPastedText(body: { title: string; text: string; author?: string }, signal?: AbortSignal): Promise<EbookDraft> {
  return apiFetch('/api/audiobooks/imports/text', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, parseEbookDraft)
}
export async function listEbookDrafts(signal?: AbortSignal): Promise<EbookDraftSummary[]> {
  return (await apiFetch('/api/audiobooks/imports', { signal }, parseEbookDraftsResponse)).drafts
}
export function getEbookDraft(id: string, signal?: AbortSignal): Promise<EbookDraft> { return apiFetch(`/api/audiobooks/imports/${encodeURIComponent(id)}`, { signal }, parseEbookDraft) }
export async function deleteEbookDraft(id: string, signal?: AbortSignal): Promise<void> { await apiFetch(`/api/audiobooks/imports/${encodeURIComponent(id)}`, { method: 'DELETE', signal }) }
export function saveEbookDraft(id: string, body: PatchEbookDraftRequest, signal?: AbortSignal): Promise<EbookDraft> {
  const validated = parsePatchEbookDraftRequest(body)
  return apiFetch(`/api/audiobooks/imports/${encodeURIComponent(id)}`, { method: 'PATCH', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(validated) }, parseEbookDraft)
}
export function createAudiobookFromDraft(draft: EbookDraft, profileId: string, signal?: AbortSignal): Promise<AudiobookCreateResponse> {
  return apiFetch(`/api/audiobooks/imports/${encodeURIComponent(draft.id)}/create`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: profileId, revision: draft.revision }) }, parseAudiobookCreateResponse)
}
export function ebookSourceUrl(id: string): string { return `/api/audiobooks/imports/${encodeURIComponent(id)}/source` }
export function controlAudiobook(id: string, action: 'pause' | 'resume' | 'cancel', signal?: AbortSignal): Promise<AudiobookBook> {
  return apiFetch(`/api/audiobooks/${encodeURIComponent(id)}/${action}`, { method: 'POST', signal }, parseAudiobookBook)
}

export async function listAudiobooks(signal?: AbortSignal): Promise<AudiobookBook[]> {
  const json = await apiFetch('/api/audiobooks', { signal }, parseAudiobookBooksResponse)
  return json.books
}

export async function getAudiobook(bookId: string, signal?: AbortSignal): Promise<AudiobookBook> {
  return apiFetch(`/api/audiobooks/${encodeURIComponent(bookId)}`, { signal }, parseAudiobookBook)
}

export async function createAudiobook(
  body: CreateAudiobookRequest,
  signal?: AbortSignal,
): Promise<AudiobookCreateResponse> {
  return apiFetch('/api/audiobooks', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, parseAudiobookCreateResponse)
}

export async function listAudiobookJobs(bookId: string, signal?: AbortSignal): Promise<AudiobookJob[]> {
  const json = await apiFetch(
    `/api/audiobooks/${encodeURIComponent(bookId)}/jobs`,
    { signal },
    parseAudiobookJobsResponse,
  )
  return json.jobs
}

export async function retryAudiobook(bookId: string, signal?: AbortSignal): Promise<AudiobookBook> {
  return apiFetch(`/api/audiobooks/${encodeURIComponent(bookId)}/retry`, {
    method: 'POST',
    signal,
  }, parseAudiobookBook)
}

export function audiobookExportUrl(bookId: string): string {
  return `/api/audiobooks/${encodeURIComponent(bookId)}/export`
}

export function audiobookExportFormatUrl(bookId: string, format: 'mp3' | 'm4b'): string {
  return `/api/audiobooks/${encodeURIComponent(bookId)}/exports/${format}`
}

export function setAudiobookPronunciations(bookId: string, pronunciations: Array<{ written: string; spoken: string }>, signal?: AbortSignal): Promise<AudiobookBook> {
  return apiFetch(`/api/audiobooks/${encodeURIComponent(bookId)}/pronunciations`, {
    method: 'PUT', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pronunciations }),
  }, parseAudiobookBook)
}

export function regenerateAudiobookChapter(bookId: string, chapterIndex: number, signal?: AbortSignal): Promise<AudiobookBook> {
  return apiFetch(`/api/audiobooks/${encodeURIComponent(bookId)}/chapters/${chapterIndex}/regenerate`, { method: 'POST', signal }, parseAudiobookBook)
}

export function uploadAudiobookCover(bookId: string, file: File, signal?: AbortSignal): Promise<AudiobookBook> {
  const form = new FormData(); form.append('file', file, file.name)
  return apiFetch(`/api/audiobooks/${encodeURIComponent(bookId)}/cover`, { method: 'POST', body: form, signal }, parseAudiobookBook)
}

export function audiobookChapterAudioUrl(bookId: string, chapterIndex: number): string {
  return `/api/audiobooks/${encodeURIComponent(bookId)}/chapters/${chapterIndex}/audio`
}

export async function fetchAudiobookExportBlob(bookId: string, signal?: AbortSignal): Promise<Blob> {
  const resp = await fetch(audiobookExportUrl(bookId), { signal })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const raw: unknown = await resp.json()
      if (raw && typeof raw === 'object' && 'detail' in raw && typeof (raw as { detail: unknown }).detail === 'string') {
        detail = (raw as { detail: string }).detail
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(detail || `HTTP ${resp.status}`, resp.status)
  }
  return resp.blob()
}
