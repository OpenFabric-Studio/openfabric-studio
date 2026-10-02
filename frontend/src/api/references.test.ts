import { afterEach, expect, it, vi } from 'vitest'
import * as api from './references'
afterEach(() => vi.unstubAllGlobals())
it('validates local preparation and uses the owned endpoint', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'a'.repeat(32), status: 'queued', request: { kind: 'track', track_id: 7, melody: true }, stages: [{ name: 'download', status: 'done' }, { name: 'lyrics', status: 'skipped' }, { name: 'separation', status: 'skipped' }, { name: 'melody', status: 'queued' }], created_at: '2026-10-01', updated_at: '2026-10-01' }))); vi.stubGlobal('fetch', fetch)
  expect((await api.prepare({ track_id: 7, melody: true })).status).toBe('queued')
  expect(fetch).toHaveBeenCalledWith('/api/references/prepare', expect.objectContaining({ method: 'POST', body: '{"track_id":7,"melody":true}' }))
  await expect(api.prepare({ track_id: 0 })).rejects.toThrow(); expect(fetch).toHaveBeenCalledTimes(1)
})
it('rejects contradictory text transformation inputs before HTTP', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch)
  await expect(api.transformAbc({ abc: 'X:1\nK:C\nC', target_min_midi: 70, target_max_midi: 50 })).rejects.toThrow()
  await expect(api.transformAbc({ abc: 'X:1\nK:C\nC', target_min_midi: 50 })).rejects.toThrow()
  await expect(api.transformLyrics({ text: 'lyrics', section_size: 0 })).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled()
})
it('validates reference IDs and server responses', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response('{}')); vi.stubGlobal('fetch', fetch)
  await expect(api.cancel('../x')).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled()
  await expect(api.list()).rejects.toThrow('Invalid ReferenceImportsResponse')
})
