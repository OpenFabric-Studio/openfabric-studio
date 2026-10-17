import { apiFetch } from './http'
import { parseLocalMusicRequest, parseMusicConditioningCapabilities, parseMusicConditioningPreview,
  parseMusicConditioningSourceInfo, parseMusicConditioningSourceRequest } from './generated'
import type { LocalMusicRequest, MusicConditioningSourceInfo, MusicConditioningSourceRequest } from './generated'

export type ConditioningSource = MusicConditioningSourceRequest['source']
export type ConditioningSelection = NonNullable<LocalMusicRequest['conditioning']>
export type MusicConditioningDraft = { source: ConditioningSource; label: string }
const base = '/api/music-conditioning'
export function conditioningSourceKey(source: ConditioningSource): string {
  if (source.kind === 'version') return `version:${source.track_id}:${source.version_id}`
  if (source.kind === 'stem') return `stem:${source.track_id}:${source.stem}:${source.sha256}`
  if (source.kind === 'production_asset') return `asset:${source.asset_id}:${source.sha256}`
  return `beat:${source.beat_id}:${source.beat_revision}:${source.render_id}:${source.sha256}`
}
export function conditioningCapabilities(signal?: AbortSignal) {
  return apiFetch(`${base}/capabilities`, { signal }, parseMusicConditioningCapabilities)
}
function boundInfo(info: MusicConditioningSourceInfo, source: ConditioningSource): MusicConditioningSourceInfo {
  if (conditioningSourceKey(info.source) !== conditioningSourceKey(source)
    || Math.abs(info.duration_sec - info.source_frames / info.source_rate_hz) > 1e-9
    || source.kind !== 'version' && info.source_sha256 !== source.sha256) throw new TypeError('Mismatched conditioning source')
  return info
}
export function inspectConditioningSource(source: ConditioningSource, signal?: AbortSignal) {
  const request = parseMusicConditioningSourceRequest({ source })
  return apiFetch(`${base}/source`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request), signal }, value => boundInfo(parseMusicConditioningSourceInfo(value), request.source))
}
export async function previewConditioning(body: LocalMusicRequest, signal?: AbortSignal) {
  const request = parseLocalMusicRequest(body), selection = request.conditioning
  if (!selection) throw new TypeError('Missing conditioning source')
  return apiFetch(`${base}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request), signal }, value => {
    const preview = parseMusicConditioningPreview(value)
    boundInfo(preview, selection.source)
    if (preview.source_sha256 !== selection.source_sha256) throw new TypeError('Mismatched conditioning digest')
    return preview
  })
}
