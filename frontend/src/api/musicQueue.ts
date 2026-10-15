import { apiFetch } from './http'
import {
  parseMusicQueueCatalog, parseMusicQueueSnapshot, parseMusicQueuePreview,
  parseMusicQueuePreviewRequest, parseMusicQueueEnqueueRequest, parseMusicQueueControlRequest,
  parseMusicQueueReorderRequest, parseMusicQueueRetryRequest, parseMusicQueueCancelRequest,
} from './generated'
import type {
  MusicQueueCatalog, MusicQueueSnapshot, MusicQueuePreview, MusicQueuePreviewRequest,
  MusicQueueEnqueueRequest, MusicQueueControlRequest, MusicQueueReorderRequest,
  MusicQueueRetryRequest, MusicQueueCancelRequest,
} from './generated'

const BASE = '/api/music-queue'
function post<T>(path: string, request: object, parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  return apiFetch(`${BASE}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal }, parse)
}
export function musicQueueCatalog(signal?: AbortSignal): Promise<MusicQueueCatalog> {
  return apiFetch(`${BASE}/catalog`, { signal }, parseMusicQueueCatalog)
}
export function getMusicQueue(signal?: AbortSignal): Promise<MusicQueueSnapshot> {
  return apiFetch(BASE, { signal }, parseMusicQueueSnapshot)
}
export async function previewMusicQueue(request: MusicQueuePreviewRequest, signal?: AbortSignal): Promise<MusicQueuePreview> {
  return post('/preview', parseMusicQueuePreviewRequest(request), parseMusicQueuePreview, signal)
}
export async function enqueueMusicQueue(request: MusicQueueEnqueueRequest, signal?: AbortSignal): Promise<MusicQueueSnapshot> {
  return post('/batches', parseMusicQueueEnqueueRequest(request), parseMusicQueueSnapshot, signal)
}
export async function controlMusicQueue(request: MusicQueueControlRequest, signal?: AbortSignal): Promise<MusicQueueSnapshot> {
  return post('/control', parseMusicQueueControlRequest(request), parseMusicQueueSnapshot, signal)
}
export async function reorderMusicQueue(request: MusicQueueReorderRequest, signal?: AbortSignal): Promise<MusicQueueSnapshot> {
  return post('/reorder', parseMusicQueueReorderRequest(request), parseMusicQueueSnapshot, signal)
}
export async function retryMusicQueue(request: MusicQueueRetryRequest, signal?: AbortSignal): Promise<MusicQueueSnapshot> {
  return post('/retry', parseMusicQueueRetryRequest(request), parseMusicQueueSnapshot, signal)
}
export async function cancelMusicQueueBatch(request: MusicQueueCancelRequest, signal?: AbortSignal): Promise<MusicQueueSnapshot> {
  const parsed = parseMusicQueueCancelRequest(request)
  return post(`/batches/${parsed.batch_id}/cancel`, parsed, parseMusicQueueSnapshot, signal)
}
