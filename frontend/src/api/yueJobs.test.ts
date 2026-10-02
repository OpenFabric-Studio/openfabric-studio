import { afterEach, expect, it, vi } from 'vitest'
import * as api from './yueJobs'
import { yueJobResponse } from '../stores/yueJobFixtures'
afterEach(() => vi.unstubAllGlobals())
it('submits the selected voice and complete settings to server-owned generation', async () => {
  const response = yueJobResponse(); const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(response))); vi.stubGlobal('fetch', fetch)
  const request = { title: 'Jazz', lyrics: 'lyrics', seed: 1, options: api.completeYueOptions({ style: 'jazz', cot: 'off' }), precision: 'q8_0' as const, voice_id: null, settings: null }
  expect(await api.submit(request)).toEqual(response)
  expect(fetch).toHaveBeenCalledWith('/api/yue-jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify(request) }))
})
it('rejects excessive native steps before a job is sent', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch)
  expect(() => api.completeYueOptions({ style: 'jazz', cot: 'off', num_inference_steps: 257 })).toThrow(TypeError)
  expect(fetch).not.toHaveBeenCalled()
})
it('rejects telemetry from another run or inconsistent native counts', async () => {
  const job = yueJobResponse(); const progress = { run_id: 'b'.repeat(32), phase: 'acoustic', current: 8, total: 7, started_ms: 1, phase_started_ms: 1, updated_ms: 2 }
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ jobs: [{ ...job, progress }] }))))
  await expect(api.list()).rejects.toBeInstanceOf(TypeError)
})
