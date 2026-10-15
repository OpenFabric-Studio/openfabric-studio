import { afterEach, expect, it, vi } from 'vitest'
import * as library from './loraDataset'

afterEach(() => vi.unstubAllGlobals())
it('rejects an invalid catalog response at the HTTP boundary', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ datasets: [{ id: '../escape' }] }))))
  await expect(library.listLoraDatasets()).rejects.toThrow()
})
it('sends workspace updates with revisions and validates before submission', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 4, selected_dataset_id: null, form: {} })))
  vi.stubGlobal('fetch', fetcher)
  await library.saveLoraWorkspace({ revision: 3, selected_dataset_id: null, form: { lora_rank: 16 } })
  expect(fetcher).toHaveBeenCalledWith('/api/lora-datasets/workspace', expect.objectContaining({ method: 'PUT', body: expect.stringContaining('"revision":3') }))
  await expect(library.createLoraDataset('')).rejects.toThrow()
  expect(fetcher).toHaveBeenCalledOnce()
})
it('uploads into a selected stable dataset rather than its renamed display name', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ audio_dir: 'datasets/managed-id', saved: ['voice.wav'], skipped: [] })))
  vi.stubGlobal('fetch', fetcher)
  await library.uploadDatasetFiles('Renamed', [new File(['audio'], 'voice.wav')], 'a'.repeat(32), 2)
  const body: unknown = fetcher.mock.calls[0]?.[1]?.body
  expect(body).toBeInstanceOf(FormData)
  if (!(body instanceof FormData)) throw new Error('Missing multipart form')
  expect(body.get('dataset_id')).toBe('a'.repeat(32))
  expect(body.get('revision')).toBe('2')
})
