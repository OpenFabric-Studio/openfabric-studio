import { afterEach, expect, it, vi } from 'vitest'
import { importMusicAdapter, listMusicAdapters, musicAdapterFileUrl, musicLoraCapabilities } from './musicLora'
const adapter = { id: 'a'.repeat(32), engine: 'stable_audio3', target: 'small-music', name: 'Piano style', sha256: 'b'.repeat(64), created_at: '2026-10-14', source_job_id: null } as const
afterEach(() => vi.unstubAllGlobals())
function response(value: unknown) { const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(value))); vi.stubGlobal('fetch', fetch); return fetch }
it('imports native SafeTensors through multipart fields and decodes the managed manifest', async () => {
  const fetch = response(adapter), file = new File(['native checkpoint'], 'piano.safetensors')
  await expect(importMusicAdapter(file, { engine: 'stable_audio3', target: 'small-music', name: 'Piano style' })).resolves.toEqual(adapter)
  const call = fetch.mock.calls[0]
  expect(call?.[0]).toBe('/api/music-lora/adapters')
  const form = call?.[1]?.body
  expect(form).toBeInstanceOf(FormData)
  if (!(form instanceof FormData)) throw new Error('Missing multipart body')
  expect(form.get('file')).toBeInstanceOf(File)
  expect(form.get('engine')).toBe('stable_audio3'); expect(form.get('target')).toBe('small-music'); expect(form.get('name')).toBe('Piano style')
})
it('rejects unsupported file types before import and validates adapter IDs for export', async () => {
  const fetch = response(adapter)
  await expect(importMusicAdapter(new File(['pickle'], 'piano.pt'), { engine: 'stable_audio3', target: 'small-music', name: 'Piano' })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
  expect(musicAdapterFileUrl(adapter.id)).toBe(`/api/music-lora/adapters/${adapter.id}/file`)
  expect(() => musicAdapterFileUrl('../private')).toThrow()
})
it('decodes incomplete history and exact runtime capabilities and rejects invented engines', async () => {
  response({ adapters: [adapter], history_incomplete: true })
  await expect(listMusicAdapters()).resolves.toMatchObject({ history_incomplete: true, adapters: [adapter] })
  response({ engines: [{ engine: 'heartmula', loading: false, training: false, reason: 'unsupported_runtime' }] })
  await expect(musicLoraCapabilities()).resolves.toMatchObject({ engines: [{ engine: 'heartmula', training: false }] })
  response({ adapters: [{ ...adapter, engine: 'invented' }], history_incomplete: false })
  await expect(listMusicAdapters()).rejects.toThrow()
})
