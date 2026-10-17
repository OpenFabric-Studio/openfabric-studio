import { afterEach, expect, it, vi } from 'vitest'
import * as api from './musicConditioning'
import type { LocalMusicRequest, MusicConditioningSourceInfo } from './generated'

const source = { kind: 'version', track_id: 42, version_id: 'a'.repeat(32) } as const
const info: MusicConditioningSourceInfo = { source, source_sha256: 'b'.repeat(64), source_bytes: 1000,
  source_rate_hz: 44100, source_channels: 2, source_frames: 220500, duration_sec: 5 }
const request: LocalMusicRequest = { engine: 'stable_audio3', prompt: 'Piano', duration_seconds: 5,
  conditioning: { mode: 'audio_to_audio', source, source_sha256: info.source_sha256, noise_level: .6 } }
afterEach(() => vi.restoreAllMocks())

it('inspects retained source identity without a model submission and forwards cancellation', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(info)))
  const signal = new AbortController().signal
  await expect(api.inspectConditioningSource(source, signal)).resolves.toEqual(info)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music-conditioning/source', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }), signal })
})

it('refuses foreign sources, inconsistent headers and malformed digests', async () => {
  for (const value of [{ ...info, source: { ...source, track_id: 43 } }, { ...info, duration_sec: 6 },
    { ...info, source_sha256: 'b'.repeat(64) + '\n' }]) {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(value)))
    await expect(api.inspectConditioningSource(source)).rejects.toThrow()
  }
})

it('binds storage review to the source digest and rejects pure-noise input before HTTP', async () => {
  const preview = { ...info, required_free_bytes: 5000, free_bytes: 10000, can_start: true }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(preview)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...preview, source_sha256: 'c'.repeat(64) })))
  await expect(api.previewConditioning(request)).resolves.toEqual(preview)
  await expect(api.previewConditioning(request)).rejects.toThrow()
  await expect(api.previewConditioning({ ...request, conditioning: { mode: 'audio_to_audio', source,
    source_sha256: info.source_sha256, noise_level: 1 } })).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(2)
})
