import { afterEach, expect, it, vi } from 'vitest'
import { cancelStableLora, getStableLora, listStableLora, stableLoraStatus, submitStableLora } from './stableLora'
const request = { name: 'Piano', variant: 'small-music', samples: [{ track_id: 42, caption: 'Piano music' }], max_steps: 100, rank: 8, learning_rate: 0.0001, seed: 0 } as const
const job = { id: 'a'.repeat(32), status: 'queued', stage: 'preparing', created_at: '2026-10-14', request: { ...request, samples: [...request.samples] }, adapter: null, error_code: null }
afterEach(() => vi.unstubAllGlobals())
function response(value: unknown) { const fetch = vi.fn(async () => new Response(JSON.stringify(value))); vi.stubGlobal('fetch', fetch); return fetch }
it('checks training-specific weights without implicitly downloading anything', async () => {
  const fetch = response({ supported: true, installed: true, small_weights_ready: true, medium_weights_ready: false })
  await expect(stableLoraStatus()).resolves.toMatchObject({ medium_weights_ready: false })
  expect(fetch).toHaveBeenCalledWith('/api/music-lora/stable-training/status', expect.anything())
})
it('validates bounded training settings and samples before submitting', async () => {
  const fetch = response(job), body = { ...request, samples: [...request.samples] }
  await expect(submitStableLora({ ...body, max_steps: 0 })).rejects.toThrow()
  await expect(submitStableLora({ ...body, learning_rate: 1 })).rejects.toThrow()
  await expect(submitStableLora({ ...body, samples: [] })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
  await expect(submitStableLora(body)).resolves.toMatchObject(job)
  expect(fetch).toHaveBeenCalledWith('/api/music-lora/stable-training/jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify(body) }))
})
it('restores history and uses validated IDs for job reads and cancellation', async () => {
  response({ jobs: [job], history_incomplete: false }); await expect(listStableLora()).resolves.toMatchObject({ jobs: [job] })
  const fetch = response({ ...job, status: 'cancelled' })
  await getStableLora(job.id); await cancelStableLora(job.id)
  expect(fetch).toHaveBeenNthCalledWith(1, `/api/music-lora/stable-training/jobs/${job.id}`, expect.anything())
  expect(fetch).toHaveBeenNthCalledWith(2, `/api/music-lora/stable-training/jobs/${job.id}/cancel`, expect.objectContaining({ method: 'POST' }))
  fetch.mockClear(); await expect(cancelStableLora('../private')).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled()
})
