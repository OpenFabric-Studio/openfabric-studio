import { afterEach, expect, it, vi } from 'vitest'
import * as api from './stemExports'
import { encodingSettings } from '../views/settings/settingsTestFixtures'
afterEach(() => vi.unstubAllGlobals())
it('prepares an MP3 of the exact original stem using global encoding settings', async () => {
  const row = { id: 'a'.repeat(32), track_id: 7, stem_name: 'vocals', format: 'mp3', status: 'queued', error_code: '', created_at: '2026-10-01', filename: null, audio_url: null, settings: encodingSettings() }
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(row))); vi.stubGlobal('fetch', fetch)
  expect(await api.create(7, 'vocals')).toEqual(row)
  expect(fetch).toHaveBeenCalledWith('/api/tracks/7/stems/vocals/exports', expect.objectContaining({ method: 'POST', body: '{"format":"mp3"}' }))
})
it('rejects invalid stem identity before issuing requests', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch)
  await expect(api.list(7, '../vocals')).rejects.toThrow(); await expect(api.list(0, 'vocals')).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled()
})
