import { apiFetch } from './http'
import { parseMusicAdapter, parseMusicAdapters, parseMusicLoraCapabilities } from './generated'
import type { MusicAdapter, MusicAdapters, MusicLoraCapabilities } from './generated'

// The backend also applies Unicode case folding and owns the atomic name check.
export function musicLoraNameKey(name: string): string { return name.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase() }

export function listMusicAdapters(signal?: AbortSignal): Promise<MusicAdapters> {
  return apiFetch('/api/music-lora/adapters', { signal }, parseMusicAdapters)
}
export function musicLoraCapabilities(signal?: AbortSignal): Promise<MusicLoraCapabilities> {
  return apiFetch('/api/music-lora/capabilities', { signal }, parseMusicLoraCapabilities)
}
export async function importMusicAdapter(file: File, metadata: Pick<MusicAdapter, 'engine' | 'target' | 'name'>, signal?: AbortSignal): Promise<MusicAdapter> {
  const name = metadata.name.trim()
  if (!/\.safetensors$/i.test(file.name) || file.size <= 8 || file.size > 512 * 1024 * 1024 || !name || name.length > 120
    || !(metadata.engine === 'stable_audio3' ? metadata.target === 'small-music' || metadata.target === 'medium' : metadata.target === 'ar' || metadata.target === 'nar')) throw new TypeError('Invalid music adapter import')
  const body = new FormData()
  body.append('file', file); body.append('engine', metadata.engine); body.append('target', metadata.target); body.append('name', name)
  return apiFetch('/api/music-lora/adapters', { method: 'POST', body, signal }, parseMusicAdapter)
}
export function musicAdapterFileUrl(id: string): string {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid music adapter ID')
  return `/api/music-lora/adapters/${id}/file`
}
