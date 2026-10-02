import { apiFetch, apiJson } from './http'
import type { TrackOrigin } from '../types'
import { parseSavedTrack, parseSetTrackFavoriteRequest, parseTracksResponse } from './contracts'
import type { SavedTrack, JsonObject } from './contracts'
export type { SavedTrack } from './contracts'

export interface SaveTrackMeta {
  model: TrackOrigin
  title: string
  lyrics: string
  seed?: number
  duration_ms?: number
  wall_ms?: number
  params?: JsonObject
}

export async function saveTrack(meta: SaveTrackMeta, audio: Blob, audioExt: string, abcText?: string | null): Promise<SavedTrack> {
  const form = new FormData()
  form.append('model', meta.model)
  form.append('title', meta.title)
  form.append('lyrics', meta.lyrics)
  if (meta.seed != null) form.append('seed', String(meta.seed))
  if (meta.duration_ms != null) form.append('duration_ms', String(meta.duration_ms))
  if (meta.wall_ms != null) form.append('wall_ms', String(meta.wall_ms))
  form.append('params', JSON.stringify(meta.params || {}))
  if (abcText) form.append('abc', abcText)
  form.append('audio', audio, `track.${audioExt}`)
  return apiFetch('/api/tracks', { method: 'POST', body: form }, parseSavedTrack)
}

export async function uploadTrack(file: File, title?: string, signal?: AbortSignal): Promise<SavedTrack> {
  const form = new FormData()
  form.append('audio', file)
  if (title) form.append('title', title)
  return apiFetch('/api/tracks/upload', { method: 'POST', body: form, signal }, parseSavedTrack)
}

export async function listTracks(model?: TrackOrigin, signal?: AbortSignal): Promise<SavedTrack[]> {
  const qs = model ? `?model=${encodeURIComponent(model)}` : ''
  const json = await apiFetch(`/api/tracks${qs}`, { signal }, parseTracksResponse)
  return json.data
}

export async function renameTrack(id: number, title: string): Promise<SavedTrack> {
  return apiJson(`/api/tracks/${id}`, { title }, 'PUT', parseSavedTrack)
}


export async function setTrackFavorite(trackId: number, isFavorite: boolean, signal?: AbortSignal): Promise<SavedTrack> {
  if (!Number.isSafeInteger(trackId) || trackId < 1) throw new Error('Invalid track ID')
  const body = parseSetTrackFavoriteRequest({ is_favorite: isFavorite })
  return apiFetch(`/api/tracks/${trackId}/favorite`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  }, parseSavedTrack)
}

export async function deleteTrack(id: number): Promise<void> {
  await apiFetch(`/api/tracks/${id}`, { method: 'DELETE' })
}

export function formatTrackCodes(codes: Array<number | null | undefined>): string {
  return codes.filter((code): code is number => typeof code === 'number' && code > 0).join(', ')
}

export function trackAudioUrl(id: number): string {
  return `/api/tracks/${id}/audio`
}

export async function trackAbc(id: number): Promise<string> {
  const resp = await fetch(`/api/tracks/${id}/abc`)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.text()
}
