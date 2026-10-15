import { apiFetch } from './http'
import { parseLocalMusicEngines, parseLocalMusicJob, parseLocalMusicJobs, parseLocalMusicRequest } from './generated'
import type { LocalMusicEngines, LocalMusicJob, LocalMusicJobs, LocalMusicRequest } from './generated'

export type LocalMusicEngineId = LocalMusicRequest['engine']

function jobPath(id: string): string {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid local music job ID')
  return `/api/local-music/jobs/${id}`
}
export function localMusicEngines(signal?: AbortSignal): Promise<LocalMusicEngines> {
  return apiFetch('/api/local-music/engines', { signal }, parseLocalMusicEngines)
}
export function listLocalMusic(signal?: AbortSignal): Promise<LocalMusicJobs> {
  return apiFetch('/api/local-music/jobs', { signal }, parseLocalMusicJobs)
}
export async function submitLocalMusic(request: LocalMusicRequest, signal?: AbortSignal): Promise<LocalMusicJob> {
  return apiFetch('/api/local-music/jobs', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseLocalMusicRequest(request)) }, parseLocalMusicJob)
}
export async function getLocalMusic(id: string, signal?: AbortSignal): Promise<LocalMusicJob> {
  return apiFetch(jobPath(id), { signal }, parseLocalMusicJob)
}
export async function cancelLocalMusic(id: string, signal?: AbortSignal): Promise<LocalMusicJob> {
  return apiFetch(`${jobPath(id)}/cancel`, { method: 'POST', signal }, parseLocalMusicJob)
}
