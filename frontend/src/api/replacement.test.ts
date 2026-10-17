import { afterEach, expect, it, vi } from 'vitest'
import * as api from './replacement'
import { replacementPlan } from '../stores/replacementTestFixtures'
afterEach(() => vi.restoreAllMocks())
it('creates a prepared plan without an unused voice target and validates the body', async () => {
  const plan = replacementPlan(), signal = new AbortController().signal
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(plan)))
  await expect(api.createReplacementPlan(plan.request, signal)).resolves.toEqual(plan)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/voices/replace/plans', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(plan.request) })
  expect(() => api.replacementRequest({ ...plan.request, target: { engine: 'seed-vc', voice_id: 'f'.repeat(32), model_revision_id: 'trained' } })).toThrow()
})
it('rejects cross-plan responses and unsafe paths before they become playable', async () => {
  const plan = replacementPlan()
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...plan, id: 'f'.repeat(32) })))
  await expect(api.getReplacementPlan(plan.id)).rejects.toThrow()
  expect(() => api.getReplacementPlan('../outside')).toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('binds preparation to a revision/action id and forwards cancellation', async () => {
  const plan = replacementPlan(), signal = new AbortController().signal, request = { expected_revision: plan.revision ?? 1, request_id: 'f'.repeat(32) }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(plan)))
  await api.prepareReplacementPlan(plan.id, request, signal)
  expect(fetch).toHaveBeenCalledExactlyOnceWith(`/api/voices/replace/plans/${plan.id}/prepare`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) })
  expect(() => api.prepareReplacementPlan(plan.id, { ...request, expected_revision: 0 })).toThrow()
})

it('binds retried creation to its explicit server identity and rejects a different returned plan', async () => {
  const plan = replacementPlan(), requestId = plan.id
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(plan))).mockResolvedValueOnce(new Response(JSON.stringify({ ...plan, id: 'f'.repeat(32) })))
  await expect(api.createReplacementPlan(plan.request, undefined, requestId)).resolves.toEqual(plan)
  expect(fetch.mock.calls[0]?.[0]).toBe(`/api/voices/replace/plans?request_id=${requestId}`)
  await expect(api.createReplacementPlan(plan.request, undefined, requestId)).rejects.toThrow()
  expect(() => api.createReplacementPlan(plan.request, undefined, '../outside')).toThrow()
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('rejects measurements from a different plan revision', async () => {
  const plan = replacementPlan()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...plan, diagnostics: {
    revision: 2, request_sha256: 'a'.repeat(64), attempt_id: 'b'.repeat(32),
    created_at: plan.updated_at, unavailable: ['pitch', 'tonal'],
  } })))
  await expect(api.getReplacementPlan(plan.id)).rejects.toThrow()
})
