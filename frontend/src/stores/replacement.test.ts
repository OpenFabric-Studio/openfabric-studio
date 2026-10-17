// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import * as api from '../api/replacement'
import { ApiError } from '../api/http'
import type { ReplacementPlanResponse } from '../api/generated'
import { useReplacementStore } from './replacement'
import { replacementCapabilities, replacementPlan } from './replacementTestFixtures'
vi.mock('../api/replacement', () => ({ replacementRequest: (value: unknown) => value, listReplacementPlans: vi.fn(), replacementCapabilities: vi.fn(), getReplacementPlan: vi.fn(), createReplacementPlan: vi.fn(), prepareReplacementPlan: vi.fn(), cancelReplacementPlan: vi.fn(), updateReplacementPlan: vi.fn(), editReplacementMix: vi.fn(), editReplacementAlignment: vi.fn(), retryReplacementPlan: vi.fn(), renderReplacement: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear(); vi.useFakeTimers(); setActivePinia(createPinia())
  const plan = replacementPlan()
  vi.mocked(api.listReplacementPlans).mockResolvedValue({ plans: [{ id: plan.id, revision: 1, mode: 'prepared', title: plan.request.title, status: 'draft', phase: 'inspecting', updated_at: plan.updated_at }] })
  vi.mocked(api.replacementCapabilities).mockResolvedValue(replacementCapabilities())
  vi.mocked(api.getReplacementPlan).mockResolvedValue(plan)
})
afterEach(() => { useReplacementStore().stop(); vi.useRealTimers() })
it('rehydrates saved prepared plans without requiring a selected voice', async () => {
  const store = useReplacementStore(); await store.start()
  expect(store.plan?.request.mode).toBe('prepared'); expect(store.plan?.request).not.toHaveProperty('target')
  expect(api.prepareReplacementPlan).not.toHaveBeenCalled()
})
it('serializes double-clicked preparation and reconnects to its running job', async () => {
  const store = useReplacementStore(); await store.start()
  let finish: ((value: ReplacementPlanResponse) => void) | undefined
  vi.mocked(api.prepareReplacementPlan).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const first = store.prepare(); await store.prepare()
  expect(api.prepareReplacementPlan).toHaveBeenCalledOnce()
  finish?.({ ...replacementPlan(), status: 'running', phase: 'aligning' }); await first
  vi.mocked(api.getReplacementPlan).mockResolvedValue({ ...replacementPlan(), status: 'needs_review', phase: 'aligning' })
  await vi.advanceTimersByTimeAsync(2500)
  expect(store.plan?.status).toBe('needs_review'); expect(store.active).toBe(false)
  expect(vi.getTimerCount()).toBe(0)
})
it('drops late loads after stopping and does not restart polling', async () => {
  const store = useReplacementStore()
  let finish: ((value: ReplacementPlanResponse) => void) | undefined
  vi.mocked(api.getReplacementPlan).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const opening = store.start(); await Promise.resolve(); await Promise.resolve()
  store.stop(); finish?.({ ...replacementPlan(), status: 'running' }); await opening
  await vi.advanceTimersByTimeAsync(6000)
  expect(store.plan).toBeNull(); expect(vi.getTimerCount()).toBe(0)
})
it('preserves the server snapshot after a revision conflict', async () => {
  const store = useReplacementStore(); await store.start()
  vi.mocked(api.updateReplacementPlan).mockRejectedValue(new ApiError('replacement_revision_conflict', 409))
  await store.update({ ...replacementPlan().request, title: 'Other title' })
  expect(store.error).toBe('conflict'); expect(store.plan?.request.title).toBe('Prepared fixture'); expect(store.plan?.revision).toBe(1)
})
it('does not start another request after a create finishes after teardown', async () => {
  const store = useReplacementStore(); await store.start(); store.newDraft()
  let finish: ((value: ReplacementPlanResponse) => void) | undefined
  vi.mocked(api.createReplacementPlan).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const creating = store.create(replacementPlan().request), before = vi.mocked(api.listReplacementPlans).mock.calls.length
  store.stop(); finish?.(replacementPlan()); await creating
  expect(api.listReplacementPlans).toHaveBeenCalledTimes(before); expect(store.plan).toBeNull(); expect(vi.getTimerCount()).toBe(0)
})

it.each([new Error('connection lost'), new ApiError('Invalid response from server', 200), new ApiError('Request timeout', 408)])('keeps uncertain action identity across a reload and replays its exact body before allowing new work (%s)', async failure => {
  let store = useReplacementStore(); await store.start()
  vi.mocked(api.prepareReplacementPlan).mockRejectedValueOnce(failure)
  await store.prepare()
  const first = vi.mocked(api.prepareReplacementPlan).mock.calls[0]?.[1]
  expect(first?.request_id).toMatch(/^[a-f0-9]{32}$/)
  store.stop(); setActivePinia(createPinia()); store = useReplacementStore(); await store.start()
  expect(store.pendingAction).not.toBeNull()
  await store.prepare(); expect(api.prepareReplacementPlan).toHaveBeenCalledTimes(1)
  vi.mocked(api.prepareReplacementPlan).mockResolvedValue({ ...replacementPlan(), status: 'running', phase: 'aligning', active_attempt_id: 'a'.repeat(32) })
  await store.resumePending()
  expect(api.prepareReplacementPlan).toHaveBeenLastCalledWith(replacementPlan().id, first, expect.any(AbortSignal))
  expect(store.pendingAction).toBeNull()
})
it('keeps failed cleanup attempts active and allows their cancel control', async () => {
  const blocked = { ...replacementPlan(), status: 'failed', error_code: 'replacement_cleanup_pending', active_attempt_id: 'a'.repeat(32) } as const
  vi.mocked(api.getReplacementPlan).mockResolvedValue(blocked)
  const store = useReplacementStore(); await store.start()
  expect(store.active).toBe(true)
  store.newDraft(); expect(store.plan?.id).toBe(blocked.id)
  vi.mocked(api.cancelReplacementPlan).mockResolvedValue({ ...replacementPlan(), status: 'cancelled', active_attempt_id: null })
  await store.cancel(); expect(api.cancelReplacementPlan).toHaveBeenCalledOnce()
})

it('retains an uncertain create across reload and reuses its exact creation identity', async () => {
  let store = useReplacementStore(); await store.start(); store.newDraft()
  vi.mocked(api.createReplacementPlan).mockRejectedValueOnce(new Error('connection lost after acceptance'))
  const original = replacementPlan().request
  await store.create(original)
  const first = vi.mocked(api.createReplacementPlan).mock.calls[0]
  expect(first?.[2]).toMatch(/^[a-f0-9]{32}$/)
  store.stop(); setActivePinia(createPinia()); store = useReplacementStore(); await store.start()
  await store.create({ ...original, title: 'Do not submit a second plan' })
  expect(api.createReplacementPlan).toHaveBeenCalledTimes(1)
  vi.mocked(api.createReplacementPlan).mockResolvedValue(replacementPlan())
  await store.resumePending()
  expect(api.createReplacementPlan).toHaveBeenLastCalledWith(first?.[0], expect.any(AbortSignal), first?.[2])
  expect(store.pendingAction).toBeNull()
})
