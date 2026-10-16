import { afterEach, expect, it, vi } from 'vitest'
import * as api from './musicTraining'
const sourceId = 'c'.repeat(32)
const request = { planner_steps: 100, planner_learning_rate: 0.00005 }
afterEach(() => vi.restoreAllMocks())
it('validates the planner settings and source job ID before sending a typed planner request', async () => {
  const body = { id: 'd'.repeat(32), status: 'queued', created_at: '2026-10-16T13:00:00Z', request: { engine: 'yue2', name: 'Folk', samples: [{ track_id: 42, caption: 'Folk' }], max_steps: 100, learning_rate: 0.00005, yue_ar_source_job_id: sourceId } }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)))
  const signal = new AbortController().signal
  await expect(api.trainMissingMusicPlanner(sourceId, request, signal)).resolves.toMatchObject(body)
  expect(fetch).toHaveBeenCalledExactlyOnceWith(`/api/music-lora/training/jobs/${sourceId}/planner`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) })
  expect(() => api.trainMissingMusicPlanner('../outside', request)).toThrow('Invalid music training job ID')
  expect(() => api.trainMissingMusicPlanner(sourceId, { ...request, planner_steps: 0 })).toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
})
