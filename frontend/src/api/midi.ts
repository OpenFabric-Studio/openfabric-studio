import { apiFetch } from './http'

import { parseMidiStatusResponse } from './contracts'
import type { MidiStatusResponse } from './contracts'

export type MidiSource = MidiStatusResponse['available'][number]
export type MidiSourceState = MidiStatusResponse['sources']['full']
export type MidiStatus = MidiStatusResponse

export function getMidiStatus(trackId: number, signal?: AbortSignal): Promise<MidiStatus> {
  return apiFetch(`/api/tracks/${trackId}/midi/status`, { signal }, parseMidiStatusResponse)
}

export function startTranscription(trackId: number, source: MidiSource, force = false): Promise<MidiStatus> {
  return apiFetch(`/api/tracks/${trackId}/midi/${source}?force=${force}`, { method: 'POST' }, parseMidiStatusResponse)
}

export function cancelTranscription(trackId: number, source: MidiSource): Promise<MidiStatus> {
  return apiFetch(`/api/tracks/${trackId}/midi/${source}/cancel`, { method: 'POST' }, parseMidiStatusResponse)
}

export async function deleteMidi(trackId: number): Promise<void> {
  await apiFetch(`/api/tracks/${trackId}/midi`, { method: 'DELETE' })
}
