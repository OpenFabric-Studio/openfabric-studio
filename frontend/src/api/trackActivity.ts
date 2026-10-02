import { apiFetch } from './http'
import { parseTrackActivityResponse } from './contracts'
export async function listActiveAudioTrackIds(signal?: AbortSignal): Promise<number[]> {
  const result = await apiFetch('/api/tracks/activity', { signal }, parseTrackActivityResponse)
  return result.track_ids
}
