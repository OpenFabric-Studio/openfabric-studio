import { afterEach, describe, expect, it, vi } from 'vitest'
import { decodeLocalEngineResult, decodeLocalEnginesStatus, localEngineMediaUrl, runChatterbox, runKokoro, runRvc, runWan } from './localEngines'

afterEach(() => vi.unstubAllGlobals())

describe('local engine media urls', () => {
  it('uses the API media url and falls back to the output path pattern', () => {
    expect(localEngineMediaUrl({ media_url: '/api/local-engines/kokoro/media/abc', output_path: null })).toBe('/api/local-engines/kokoro/media/abc')
    const id = 'ab'.repeat(16)
    expect(localEngineMediaUrl({ media_url: null, output_path: `/data/outputs/local-engines/wan22/${id}.mp4` })).toBe(`/api/local-engines/wan22/media/${id}`)
    expect(localEngineMediaUrl({ media_url: null, output_path: '/tmp/not-a-local-engine.wav' })).toBeNull()
  })
})

it('accepts optional result fields exactly as the backend contract declares them', () => {
  expect(decodeLocalEngineResult({ status: 'completed', detail: 'Done' })).toEqual({ status: 'completed', detail: 'Done' })
})

it('validates bounded request text and paths before starting an engine call', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ status: 'completed', detail: 'Done', runtime: 'cpu', output_path: null, media_url: null })))
  vi.stubGlobal('fetch', fetch)
  await expect(runKokoro({ text: 'x'.repeat(4001), voice: 'af_heart', lang: 'a' })).rejects.toThrow()
  await expect(runChatterbox({ text: '', model: 'original' })).rejects.toThrow()
  await expect(runRvc({ model_path: 'x'.repeat(1001), input_path: '/audio.wav' })).rejects.toThrow()
  await expect(runWan({ prompt: 'x'.repeat(2001) })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('sends the supported Wan request and decodes its generated response contract', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ status: 'completed', detail: 'Done', media_url: '/api/local-engines/wan22/media/abc' })))
  vi.stubGlobal('fetch', fetch)
  await expect(runWan({ prompt: 'A still scene', image_path: '/image.png' })).resolves.toMatchObject({ status: 'completed', media_url: '/api/local-engines/wan22/media/abc' })
  expect(fetch).toHaveBeenCalledWith('/api/local-engines/wan', expect.objectContaining({ method: 'POST', body: JSON.stringify({ engine: 'wan22', variant: 'ti2v-5b', prompt: 'A still scene', image_path: '/image.png' }) }))
})

it('rejects unknown engine IDs and malformed status fields', () => {
  const status = { video_engine: 'ltx', video_preference: 'ltx', note: '', engines: [{ id: 'unknown-engine', installed: true, setup_script: '', runtime: 'cpu', voices: [], languages: [] }] }
  expect(() => decodeLocalEnginesStatus(status)).toThrow()
  expect(() => decodeLocalEnginesStatus({ ...status, engines: [], video_engine: 'wan' })).toThrow()
})
