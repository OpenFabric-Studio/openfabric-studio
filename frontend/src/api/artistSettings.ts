import { apiFetch } from './http'
import { parseArtistSettings } from './generated'
export type { ArtistSettings } from './generated'

export async function getArtistSettings(signal?: AbortSignal) {
  return parseArtistSettings(await apiFetch('/api/settings', { signal }))
}

export async function saveArtistSettings(artist: string, signal?: AbortSignal) {
  const body = parseArtistSettings({ artist })
  return parseArtistSettings(await apiFetch('/api/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  }))
}
