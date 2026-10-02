import { afterEach, expect, it, vi } from 'vitest'
import { listAudioVersions, createAudioVersion, cancelAudioVersion, retryAudioVersion } from './audioVersions'
import type { AudioVersion } from './contracts'

afterEach(() => vi.unstubAllGlobals())
const version: AudioVersion = { id: 'a'.repeat(32), track_id: 42, kind: 'voice', voice_id: 'b'.repeat(32), voice_name: 'Singer', status: 'queued', created_at: '2026-10-01T12:00:00Z', filename: null, audio_url: null, error_code: '', duration_ms: 2000, source_version_id: 'c'.repeat(32) }
function respond(payload: unknown) { const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }))); vi.stubGlobal('fetch', fetch); return fetch }

it('validates the version list and forwards its abort signal', async () => {
  const fetch = respond({ track_id: 42, original_available: true, versions: [version] })
  const signal = new AbortController().signal
  expect((await listAudioVersions(42, signal)).versions?.[0]?.voice_name).toBe('Singer')
  expect(fetch).toHaveBeenCalledWith('/api/tracks/42/versions', expect.objectContaining({ signal }))
})
it('rejects malformed version responses instead of inventing playable results', async () => {
  respond({ track_id: 42, original_available: true, versions: [{ ...version, status: 'ready' }] })
  await expect(listAudioVersions(42)).rejects.toThrow()
})
it('posts a validated voice ID and exposes independent cancel/retry actions', async () => {
  const fetch = respond(version)
  await createAudioVersion(42, 'b'.repeat(32))
  expect(fetch).toHaveBeenLastCalledWith('/api/tracks/42/versions', expect.objectContaining({ method: 'POST', body: JSON.stringify({ voice_id: 'b'.repeat(32) }) }))
  await cancelAudioVersion(42, version.id)
  expect(fetch).toHaveBeenLastCalledWith(`/api/tracks/42/versions/${version.id}/cancel`, expect.objectContaining({ method: 'POST' }))
  await retryAudioVersion(42, version.id)
  expect(fetch).toHaveBeenLastCalledWith(`/api/tracks/42/versions/${version.id}/retry`, expect.objectContaining({ method: 'POST' }))
})
it('rejects unsafe identifiers before sending a request', async () => {
  const fetch = respond(version)
  await expect(createAudioVersion(42, '../private')).rejects.toThrow()
  await expect(cancelAudioVersion(42, '../private')).rejects.toThrow()
  await expect(listAudioVersions(-1)).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})
