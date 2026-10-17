// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import * as api from '../api/mashups'
import { ApiError } from '../api/http'
import { mashupJob, mashupState } from './mashupTestFixtures'
import { useMashupStore } from './mashups'
import type { MashupState } from '../api/generated'
vi.mock('../api/mashups', () => ({ listMashups: vi.fn(), getMashup: vi.fn(), listMashupJobs: vi.fn(), createMashup: vi.fn(), updateMashupTiming: vi.fn(), generateMashupPlan: vi.fn(), editMashupPlan: vi.fn(), startMashupRender: vi.fn(), cancelMashup: vi.fn(), cancelMashupJob: vi.fn(), retryMashupPreparation: vi.fn(), retryMashupSave: vi.fn() }))
beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); setActivePinia(createPinia()); vi.mocked(api.listMashups).mockResolvedValue({ documents: [mashupState()] }); vi.mocked(api.getMashup).mockResolvedValue(mashupState()); vi.mocked(api.listMashupJobs).mockResolvedValue({ jobs: [mashupJob()] }) })
afterEach(() => { useMashupStore().stop(); vi.useRealTimers() })
it('rehydrates a saved plan and render without starting work', async () => {
  const store = useMashupStore(); await store.start()
  expect(store.document?.id).toBe(mashupState().id); expect(store.jobs[0]?.status).toBe('done')
  expect(api.startMashupRender).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0)
})
it('drops a stale load after navigation and does not cancel backend work', async () => {
  const store = useMashupStore(); let finish: ((value: MashupState) => void) | undefined
  vi.mocked(api.getMashup).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const opening = store.start(); for (let n = 0; n < 5; n++) await Promise.resolve()
  store.stop(); finish?.({ ...mashupState(), status: 'preparing' }); await opening; await vi.advanceTimersByTimeAsync(6000)
  expect(store.document).toBeNull(); expect(api.cancelMashup).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0)
})
it('keeps the saved plan intact on revision conflict', async () => {
  const store = useMashupStore(); await store.start(); const snapshot = store.document
  vi.mocked(api.editMashupPlan).mockRejectedValue(new ApiError('mashup_revision_conflict', 409))
  await store.savePlacements(mashupState().plan?.placements ?? [])
  expect(store.error).toBe('conflict'); expect(store.document).toEqual(snapshot)
})
it('serializes repeated render actions and observes the accepted job', async () => {
  const store = useMashupStore(); await store.start(); let finish: ((value: ReturnType<typeof mashupJob>) => void) | undefined
  const running = { ...mashupJob(), status: 'running' as const }
  vi.mocked(api.startMashupRender).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const request = { expected_revision: 1, submission_key: 'same-request', mode: 'full' as const }
  const first = store.render(request); await store.render(request); expect(api.startMashupRender).toHaveBeenCalledOnce()
  finish?.(running); await first; expect(store.active).toBe(true)
  vi.mocked(api.listMashupJobs).mockResolvedValue({ jobs: [mashupJob()] }); await vi.advanceTimersByTimeAsync(2500)
  expect(store.active).toBe(false); expect(vi.getTimerCount()).toBe(0)
})
