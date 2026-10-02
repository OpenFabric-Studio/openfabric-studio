import { apiFetch } from './http'

import { parseStemsStatusResponse } from './contracts'
import type { StemsStatusResponse } from './contracts'

export type StemsStatus = Required<StemsStatusResponse>

function parseStemsStatus(value: unknown): StemsStatus {
  const row = parseStemsStatusResponse(value)
  return { ...row, stems: row.stems ?? null }
}

export function startSeparation(trackId: number, force = false): Promise<StemsStatus> {
  return apiFetch(`/api/tracks/${trackId}/stems?force=${force}`, { method: 'POST' }, parseStemsStatus)
}

export function cancelSeparation(trackId: number): Promise<StemsStatus> {
  return apiFetch(`/api/tracks/${trackId}/stems/cancel`, { method: 'POST' }, parseStemsStatus)
}

export function getSeparationStatus(trackId: number, signal?: AbortSignal): Promise<StemsStatus> {
  return apiFetch(`/api/tracks/${trackId}/stems/status`, { signal }, parseStemsStatus)
}

export async function deleteStems(trackId: number): Promise<void> {
  await apiFetch(`/api/tracks/${trackId}/stems`, { method: 'DELETE' })
}
