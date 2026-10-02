import { apiFetch, apiJson } from './http'
import type { MixSettings } from '../audio/mixerEngine'
import { parseMixSettingsResponse } from './contracts'
import { parseMixSettings } from './editorValidation'

function parseMixResponse(value: unknown): { settings: MixSettings | null } {
  const row = parseMixSettingsResponse(value)
  return { settings: row.settings === null ? null : parseMixSettings(row.settings) }
}

function parseSavedMix(value: unknown): { settings: MixSettings } {
  const row = parseMixResponse(value)
  if (row.settings === null) throw new TypeError('Invalid saved mix response')
  return { settings: row.settings }
}

export function getMixSettings(trackId: number): Promise<{ settings: MixSettings | null }> {
  return apiFetch(`/api/tracks/${trackId}/mix`, undefined, parseMixResponse)
}

export function saveMixSettings(trackId: number, settings: MixSettings): Promise<{ settings: MixSettings }> {
  return apiJson(`/api/tracks/${trackId}/mix`, parseMixSettings(settings), 'PUT', parseSavedMix)
}
