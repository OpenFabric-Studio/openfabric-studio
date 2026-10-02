import { parseTaggedDownloadOptions } from './generated'
import type { TaggedDownloadOptions } from './generated'
import { ApiError } from './http'
import { isObject } from './schemaValidation'
export type { TaggedDownloadOptions } from './generated'

export interface TrackAudioDownload {
  blob: Blob
  filename: string
}

export function taggedTrackDownloadUrl(trackId: number, versionId: string, exportId?: string, options?: TaggedDownloadOptions): string {
  if (!Number.isSafeInteger(trackId) || trackId < 1) throw new TypeError('Invalid track ID')
  if (!/^[0-9a-f]{32}$/.test(versionId)) throw new TypeError('Invalid audio version ID')
  if (exportId !== undefined && !/^[0-9a-f]{32}$/.test(exportId)) throw new TypeError('Invalid audio export ID')
  const profile = parseTaggedDownloadOptions(options ?? {})
  const base = `/api/tracks/${trackId}/versions/${versionId}`
  const path = exportId === undefined ? `${base}/download` : `${base}/exports/${exportId}/download`
  const query = new URLSearchParams()
  if (profile.album !== undefined && profile.album !== null) query.set('album', profile.album)
  if (profile.track_no !== undefined && profile.track_no !== null) query.set('track_no', String(profile.track_no))
  return query.size ? `${path}?${query}` : path
}

function filenameFromHeader(value: string | null, trackId: number): string {
  const fallback = `track-${trackId}`
  if (!value || value.length > 4096) return fallback
  const encoded = /filename\*=utf-8''([^;]+)/i.exec(value)?.[1]
  const plain = /filename="([^"]+)"/i.exec(value)?.[1]
  let name = plain ?? fallback
  if (encoded) {
    try { name = decodeURIComponent(encoded) } catch { return fallback }
  }
  return name.replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, '_').slice(0, 240).trim() || fallback
}

async function failureCode(response: Response): Promise<string> {
  try {
    const value: unknown = await response.json()
    if (isObject(value) && typeof value.detail === 'string' && /^[a-z_]{1,80}$/.test(value.detail)) return value.detail
  } catch {
    // Never expose raw server error bodies to the interface.
  }
  return 'download_failed'
}

/** Fetch only the explicitly selected audio. The caller owns its Blob URL. */
export async function downloadTrackAudio(trackId: number, versionId: string, exportId?: string,
  options?: TaggedDownloadOptions, signal?: AbortSignal): Promise<TrackAudioDownload> {
  const response = await fetch(taggedTrackDownloadUrl(trackId, versionId, exportId, options), { signal })
  if (!response.ok) throw new ApiError(await failureCode(response), response.status)
  const blob = await response.blob()
  if (blob.size === 0) throw new ApiError('download_empty', response.status)
  return { blob, filename: filenameFromHeader(response.headers.get('Content-Disposition'), trackId) }
}
