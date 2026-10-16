import { afterEach, expect, it, vi } from 'vitest'
import * as api from './trainingCaptions'

afterEach(() => vi.restoreAllMocks())
it('validates sources, bounds and responses through generated contracts', async () => {
  const request = { samples: [{ track_id: 42 }], segment_seconds: 12 }
  const body = { id: 'a'.repeat(32), status: 'done', request, items: [{ source: { track_id: 42 }, status: 'done', caption: 'Warm guitar' }], created_at: 'now', updated_at: 'now' }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)))
  const signal = new AbortController().signal
  await expect(api.submitTrainingCaptions(request, signal)).resolves.toMatchObject(body)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music-lora/captions/jobs', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) })
  expect(() => api.getTrainingCaptions('../outside')).toThrow('Invalid caption job ID')
  expect(() => api.submitTrainingCaptions({ ...request, segment_seconds: 600 })).toThrow()
  expect(() => api.submitTrainingCaptions({ ...request, samples: [] })).toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
  fetch.mockResolvedValue(new Response(JSON.stringify({ ...body, items: [{ source: { track_id: 42 }, status: 'done', caption: 42 }] })))
  await expect(api.getTrainingCaptions(body.id)).rejects.toThrow()
})
