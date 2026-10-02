import { apiFetch, ApiError } from './http'
import {
  parseAudiobookBook,
  parseAudiobookBooksResponse,
  parseAudiobookCreateResponse,
  parseAudiobookJobsResponse,
} from './contracts'
import type {
  AudiobookBook,
  AudiobookCreateResponse,
  AudiobookJob,
  CreateAudiobookRequest,
} from './contracts'

export type { AudiobookBook, AudiobookJob, AudiobookCreateResponse, CreateAudiobookRequest }

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
