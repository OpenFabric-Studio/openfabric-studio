import { describe, expect, it } from 'vitest'
import { localEngineMediaUrl } from './localEngines'

describe('local engine media urls', () => {
  it('uses the API media url and falls back to the output path pattern', () => {
    expect(localEngineMediaUrl({ media_url: '/api/local-engines/kokoro/media/abc', output_path: null })).toBe('/api/local-engines/kokoro/media/abc')
    const id = 'ab'.repeat(16)
    expect(localEngineMediaUrl({ media_url: null, output_path: `/data/outputs/local-engines/wan22/${id}.mp4` })).toBe(`/api/local-engines/wan22/media/${id}`)
    expect(localEngineMediaUrl({ media_url: null, output_path: '/tmp/not-a-local-engine.wav' })).toBeNull()
  })
})
