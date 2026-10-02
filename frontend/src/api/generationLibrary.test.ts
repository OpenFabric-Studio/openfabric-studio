import { afterEach, expect, it, vi } from 'vitest'
import * as api from './generationLibrary'

afterEach(() => vi.unstubAllGlobals())
it('loads filtered history using a validated response and owned request signal', async () => {
  const response = { data: [], total: 0, retention_limit: 100 }
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(response))); vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.listHistory({ engine: 'yue2', search: 'jazz', offset: 100 }, controller.signal)).toEqual(response)
  expect(fetch).toHaveBeenCalledWith('/api/generation/history?engine=yue2&search=jazz&limit=100&offset=100', { signal: controller.signal })
})
it('rejects malformed history at the HTTP boundary', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ data: [{ id: 'bad' }], total: 1, retention_limit: 100 }))))
  await expect(api.listHistory()).rejects.toBeInstanceOf(TypeError)
})
it('validates retention limits before issuing writes', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch)
  await expect(api.updateSettings({ history_limit: 0, revision: 1 })).rejects.toBeInstanceOf(TypeError)
  await expect(api.updateSettings({ history_limit: 1.5, revision: 1 })).rejects.toBeInstanceOf(TypeError)
  expect(fetch).not.toHaveBeenCalled()
})
it('uses revision checks when deleting a saved preset', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ deleted: true }))); vi.stubGlobal('fetch', fetch)
  const id = 'a'.repeat(32)
  await api.deletePreset(id, 3)
  expect(fetch).toHaveBeenCalledWith(`/api/generation/presets/${id}?revision=3`, { method: 'DELETE', signal: undefined })
})
it('loads bounded permanent preset pages with a validated total', async () => {
  const response = { data: [], total: 101 }; const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(response))); vi.stubGlobal('fetch', fetch)
  expect(await api.listPresets({ engine: 'yue2', offset: 100 })).toEqual(response)
  expect(fetch).toHaveBeenCalledWith('/api/generation/presets?engine=yue2&limit=100&offset=100', expect.anything())
})
