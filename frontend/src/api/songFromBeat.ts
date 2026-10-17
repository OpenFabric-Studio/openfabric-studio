import { apiFetch } from './http'
import { parseSongFromBeatCapabilities } from './generated'
import type { AceCompleteConditioning, AceLegoConditioning } from './generated'

export type AceSongConditioning = AceCompleteConditioning | AceLegoConditioning
export type AceSongDraft = { source: AceSongConditioning['source']; label: string }
export function songFromBeatCapabilities(signal?: AbortSignal) {
  return apiFetch('/api/song-from-beat/capabilities', { signal }, value => {
    const result = parseSongFromBeatCapabilities(value)
    if (new Set(result.modes.map(row => row.mode)).size !== 2) throw new TypeError('Mismatched song task capabilities')
    return result
  })
}
