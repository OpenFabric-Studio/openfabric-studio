import { afterEach, expect, it, vi } from 'vitest'
import { createAudioExport, listAudioExports, cancelAudioExport, retryAudioExport } from './audioExports'
import type { AudioExportResponse } from './contracts'
afterEach(() => vi.unstubAllGlobals())
const versionId = 'a'.repeat(32)
const exported: AudioExportResponse = { id: 'b'.repeat(32), track_id: 42, version_id: versionId, format: 'mp3', status: 'done', created_at: '2026-10-01T12:00:00Z', error_code: '', filename: 'song.mp3', audio_url: '/export.mp3', settings: { mp3: { mode: 'cbr', bitrate_kbps: 320, vbr_quality: 2, sample_rate: 48000, channels: 2 }, wav: { bit_depth: 24, sample_rate: 48000, channels: 2 }, flac: { bit_depth: 24, compression_level: 5, sample_rate: 48000, channels: 2 } } }
function respond(payload: unknown) { const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(payload)))); vi.stubGlobal('fetch', fetch); return fetch }
it('creates an export for the specified version using server settings', async () => {
  const fetch = respond(exported)
  expect((await createAudioExport(42, versionId, 'mp3')).settings.mp3?.bitrate_kbps).toBe(320)
  expect(fetch).toHaveBeenCalledWith(`/api/tracks/42/versions/${versionId}/exports`, expect.objectContaining({ method: 'POST', body: JSON.stringify({ format: 'mp3' }) }))
})
it('validates export lists and exposes owned cancel/retry endpoints', async () => {
  const fetch = respond({ exports: [exported] })
  expect((await listAudioExports(42, versionId)).exports?.[0]?.version_id).toBe(versionId)
  const actions = respond(exported)
  await cancelAudioExport(42, versionId, exported.id)
  await retryAudioExport(42, versionId, exported.id)
  expect(fetch).toHaveBeenCalledOnce()
  expect(actions).toHaveBeenNthCalledWith(1, `/api/tracks/42/versions/${versionId}/exports/${exported.id}/cancel`, expect.objectContaining({ method:'POST' }))
  expect(actions).toHaveBeenNthCalledWith(2, `/api/tracks/42/versions/${versionId}/exports/${exported.id}/retry`, expect.objectContaining({ method:'POST' }))
})
it('rejects invalid identifiers before network access and malformed settings after it', async () => {
  const fetch = respond(exported)
  await expect(listAudioExports(42, '../private')).rejects.toThrow()
  await expect(listAudioExports(0, versionId)).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
  respond({ ...exported, settings: { mp3: { bitrate_kbps: 999 } } })
  await expect(createAudioExport(42, versionId, 'mp3')).rejects.toThrow()
})
