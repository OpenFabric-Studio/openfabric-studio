import { afterEach, expect, it, vi } from 'vitest'
import { cancelLocalMusic, getLocalMusic, listLocalMusic, localMusicEngines, submitLocalMusic } from './localMusic'

const request = { engine: 'heartmula', title: '', prompt: 'jazz, piano', lyrics: '[Verse]\nA quiet evening', duration_seconds: 30, seed: 0, variant: 'small-music' } as const
const job = { id: 'a'.repeat(32), status: 'queued', created_at: '2026-10-14', request, track: null, error_code: null }
afterEach(() => vi.unstubAllGlobals())
function response(value: unknown) { const fetch = vi.fn(async () => new Response(JSON.stringify(value))); vi.stubGlobal('fetch', fetch); return fetch }

it('uses the music engines endpoint and rejects unrecognised response engines', async () => {
  const fetch = response({ engines: [{ id: 'heartmula', installed: true, weights_ready: false, supported: true, runtime: 'mlx', detail: 'Missing weights' }] })
  await expect(localMusicEngines()).resolves.toMatchObject({ engines: [{ id: 'heartmula', weights_ready: false }] })
  expect(fetch).toHaveBeenCalledWith('/api/local-music/engines', expect.anything())
  response({ engines: [{ id: 'unknown', installed: true, weights_ready: true, supported: true, runtime: 'cpu', detail: '' }] })
  await expect(localMusicEngines()).rejects.toThrow()
})

it('validates the request contract before submitting generation', async () => {
  const fetch = response(job)
  await expect(submitLocalMusic({ ...request, duration_seconds: 0 })).rejects.toThrow()
  await expect(submitLocalMusic({ ...request, prompt: '' })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
  await expect(submitLocalMusic(request)).resolves.toMatchObject(job)
  expect(fetch).toHaveBeenCalledWith('/api/local-music/jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify(request) }))
})

it('restores persisted history and uses the owned job endpoints', async () => {
  response({ jobs: [job], history_incomplete: true })
  await expect(listLocalMusic()).resolves.toMatchObject({ jobs: [job], history_incomplete: true })
  const fetch = response({ ...job, status: 'cancelled' })
  await getLocalMusic(job.id)
  await cancelLocalMusic(job.id)
  expect(fetch).toHaveBeenNthCalledWith(1, `/api/local-music/jobs/${job.id}`, expect.anything())
  expect(fetch).toHaveBeenNthCalledWith(2, `/api/local-music/jobs/${job.id}/cancel`, expect.objectContaining({ method: 'POST' }))
})

it('rejects malformed jobs and unsafe job IDs before making a request', async () => {
  const fetch = response({ jobs: [{ ...job, status: 'invented' }], history_incomplete: false })
  await expect(listLocalMusic()).rejects.toThrow()
  fetch.mockClear()
  await expect(getLocalMusic('../private')).rejects.toThrow()
  await expect(cancelLocalMusic('')).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})
