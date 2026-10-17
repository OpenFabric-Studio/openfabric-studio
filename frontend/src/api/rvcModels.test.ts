// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { importRvcModel, listRvcModels } from './rvcModels'
import { rvcImport } from '../views/music/rvcModelTestFixture'
afterEach(() => vi.unstubAllGlobals())
it('rejects missing trust acknowledgements and wrong checkpoint types before upload', () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  expect(() => importRvcModel({ ...rvcImport(), trusted_local_checkpoint: false }, new File(['model'], 'voice.pth'))).toThrow()
  expect(() => importRvcModel(rvcImport(), new File(['model'], 'voice.zip'))).toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})
it('sends bounded opaque files with typed evidence and no manual content-type', async () => {
  const metadata = rvcImport(), entry = { ...metadata, id: 'b'.repeat(32), checkpoint_sha256: 'c'.repeat(64), created_at: '2026-10-17T12:00:00Z' }
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(entry))); vi.stubGlobal('fetch', fetcher)
  const signal = new AbortController().signal; await importRvcModel(metadata, new File(['model'], 'voice.pth'), undefined, signal)
  const init: unknown = fetcher.mock.calls[0]?.[1]
  expect(init).toMatchObject({ method: 'POST', signal }); expect(init).not.toHaveProperty('headers')
  if (init === null || typeof init !== 'object' || !('body' in init) || !(init.body instanceof FormData)) throw new Error('Missing multipart body')
  expect(JSON.parse(String(init.body.get('metadata')))).toEqual(metadata)
})
it('validates registry responses without treating unknown readiness as ready', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ models: [{ ...rvcImport(), id: 'b'.repeat(32), checkpoint_sha256: 'c'.repeat(64), created_at: '2026-10-17T12:00:00Z', ready: false }] }))))
  expect((await listRvcModels()).models?.[0]?.ready).toBe(false)
})
