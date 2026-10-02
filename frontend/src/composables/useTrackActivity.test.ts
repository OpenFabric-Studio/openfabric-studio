// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { useTrackActivity } from './useTrackActivity'
import * as api from '../api/trackActivity'
vi.mock('../api/trackActivity', () => ({ listActiveAudioTrackIds: vi.fn() }))
let app: App | undefined
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); vi.mocked(api.listActiveAudioTrackIds).mockResolvedValue([42]) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let i = 0; i < 5; i++) await nextTick() }
async function mount() {
  app = createApp({ setup() { const activity = useTrackActivity(); return () => h('p', { 'data-failed': String(activity.error.value) }, [...activity.activeTrackIds.value].join(',')) } })
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle(); return node
}
it('discovers active tracks and retains the last known jobs on a polling failure', async () => {
  const node = await mount(); expect(node.textContent).toBe('42')
  vi.mocked(api.listActiveAudioTrackIds).mockRejectedValueOnce(new Error('Offline'))
  await vi.advanceTimersByTimeAsync(2500); await settle(); expect(node.textContent).toBe('42'); expect(node.querySelector('p')?.getAttribute('data-failed')).toBe('true')
  vi.mocked(api.listActiveAudioTrackIds).mockResolvedValue([])
  await vi.advanceTimersByTimeAsync(2500); await settle(); expect(node.textContent).toBe(''); expect(node.querySelector('p')?.getAttribute('data-failed')).toBe('false')
})
it('aborts outstanding activity requests and ignores late responses after teardown', async () => {
  let resolve: (ids: number[]) => void = () => { throw new Error('Missing pending request') }
  vi.mocked(api.listActiveAudioTrackIds).mockReturnValueOnce(new Promise<number[]>(done => { resolve = done }))
  const node = await mount(), signal = vi.mocked(api.listActiveAudioTrackIds).mock.calls[0]?.[0]
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  resolve([99]); await settle(); await vi.advanceTimersByTimeAsync(5000)
  expect(node.textContent).toBe(''); expect(api.listActiveAudioTrackIds).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0)
})
