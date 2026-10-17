import { afterEach, expect, it, vi } from 'vitest'
import { getMashup, getMashupJob, mashupArtifactUrl, previewMashupSource } from './mashups'
import type { VersionMusicSource } from './generated'
const id = 'a'.repeat(32), other = 'b'.repeat(32)
afterEach(() => vi.unstubAllGlobals())
it('rejects untrusted IDs before issuing a request', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  expect(() => getMashup('../private')).toThrow()
  expect(() => mashupArtifactUrl(id, 'https://elsewhere/')).toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})
it('validates source previews and request bounds at the shared schema boundary', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ sha256: 'f'.repeat(64), sample_rate_hz: 48000, channels: 2, duration_sec: 8, source_bytes: 100, retained_bytes: 100, temporary_bytes: 100, required_free_bytes: 1000, free_bytes: 500, can_start: false, separator_ready: false })))
  vi.stubGlobal('fetch', fetcher)
  const source: VersionMusicSource = { kind: 'version', track_id: 1, version_id: id }
  expect(() => previewMashupSource({ source, excerpt_start_sec: 0, excerpt_end_sec: 30000 })).toThrow()
  const result = await previewMashupSource({ source, excerpt_start_sec: 0, excerpt_end_sec: 8 })
  expect(result.can_start).toBe(false); expect(result.separator_ready).toBe(false)
  expect(fetcher).toHaveBeenCalledOnce()
})
it('refuses a response for another saved job', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: other, mashup_id: id, mashup_revision: 1, kind: 'full', status: 'queued', progress: { phase: 'queued' }, created_at: '2026-10-17T12:00:00+00:00' }))))
  await expect(getMashupJob(id)).rejects.toThrow()
})
