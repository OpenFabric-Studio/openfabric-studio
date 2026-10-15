// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, type App } from 'vue'
import { i18n } from '../../i18n'
import { parseStableLoraTrainingJob } from '../../api/generated'
import TrainingJobProgress from './TrainingJobProgress.vue'

let app: App | undefined
const now = '2026-10-15T21:10:00Z'
function job(overrides: Record<string, unknown> = {}) {
  return parseStableLoraTrainingJob({ id: 'c'.repeat(32), status: 'running', stage: 'training', created_at: '2026-10-15T21:00:00Z', started_at: '2026-10-15T21:00:05Z', request: { name: 'Piano', variant: 'medium', samples: [{ track_id: 42, caption: 'Piano' }], max_steps: 1000, rank: 32, learning_rate: 0.0001, seed: 42 }, progress: { stage: 'training', step: 100, total_steps: 1000, loss: 0.42, seconds_per_step: 2, updated_at: now }, ...overrides })
}
async function mount(value = job()) {
  vi.useFakeTimers(); vi.setSystemTime(now)
  const props = reactive({ job: value, reconnecting: false })
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TrainingJobProgress, props) }).use(i18n); app.mount(node); await nextTick()
  return { node, props }
}
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
it('shows measured steps, loss, elapsed time, training ETA and original run settings', async () => {
  const { node } = await mount()
  const clear = vi.spyOn(globalThis, 'clearInterval')
  expect(node.textContent).toContain('10:00')
  expect(node.textContent).toContain('~30:00')
  expect(node.textContent).toContain('100 / 1000')
  expect(node.textContent).toContain('0.4200')
  expect(node.textContent).toContain('2.0 s / step')
  expect(node.querySelector('progress')?.getAttribute('value')).toBe('100')
  expect(node.textContent).toContain('Medium')
  expect(node.textContent).toContain('32')
  await vi.advanceTimersByTimeAsync(1000)
  expect(node.textContent).toContain('10:01')
  app?.unmount(); app = undefined
  expect(clear).toHaveBeenCalledOnce()
  clear.mockRestore()
})
it.each(['preparing', 'encoding', 'loading', 'publishing'] as const)('does not estimate unmeasured %s work', async stage => {
  const { node } = await mount(job({ stage, progress: { stage, step: 100, total_steps: 1000, seconds_per_step: 2, updated_at: now } }))
  expect(node.textContent).not.toContain('~30:00')
  expect(node.textContent).toContain('ETA covers training steps only')
})
it('waits for multiple steps and hides a stale or disconnected estimate', async () => {
  const { node, props } = await mount(job({ progress: { stage: 'training', step: 1, total_steps: 1000, seconds_per_step: 2, updated_at: now } }))
  expect(node.textContent).toContain('Measuring first steps')
  props.job = job(); await nextTick()
  expect(node.textContent).toContain('~30:00')
  props.reconnecting = true; await nextTick()
  expect(node.textContent).not.toContain('~30:00')
  props.reconnecting = false; await nextTick()
  await vi.advanceTimersByTimeAsync(121000)
  expect(node.textContent).not.toContain('~30:00')
  expect(node.textContent).toContain('Waiting for a new step report')
})
it.each(['not-a-date', '2099-10-15T21:10:00Z'])('does not estimate from an invalid or future report timestamp: %s', async updated => {
  const { node } = await mount(job({ progress: { stage: 'training', step: 100, seconds_per_step: 2, updated_at: updated } }))
  expect(node.querySelector('[data-training-eta]')?.textContent).toContain('Waiting for a new step report')
})
it.each(['done', 'failed', 'cancelled', 'interrupted'] as const)('freezes elapsed time for a %s run', async status => {
  const { node } = await mount(job({ status, finished_at: '2026-10-15T21:05:00Z' }))
  expect(node.textContent).toContain('5:00')
  expect(node.textContent).not.toContain('~30:00')
  await vi.advanceTimersByTimeAsync(30000)
  expect(node.textContent).toContain('5:00')
  expect(vi.getTimerCount()).toBe(0)
})
it('supports older jobs without inventing completed duration or step measurements', async () => {
  const older = job({ status: 'done', finished_at: null })
  delete older.progress
  const { node } = await mount(older)
  expect(node.querySelector('[data-training-elapsed]')?.textContent).toBe('—')
  expect(node.querySelector('progress')).toBeNull()
  expect(node.textContent).toContain('—')
})
it('explains a previous backend without suggesting interruption of its active run', async () => {
  const older = job(); delete older.progress
  const { node } = await mount(older)
  expect(node.textContent).toContain('restart the app after it finishes')
  expect(node.textContent).not.toContain('Measuring first steps')
})
it('shows queue time without training ETA and stops the clock when a running job completes', async () => {
  const { node, props } = await mount(job({ status: 'queued' }))
  expect(node.textContent).toContain('Time in queue')
  expect(node.querySelector('[data-training-eta]')?.textContent).toBe('Waiting to start')
  props.job = job(); await nextTick()
  expect(node.querySelector('[data-training-eta]')?.textContent).toBe('~30:00')
  const clear = vi.spyOn(globalThis, 'clearInterval')
  props.job = job({ status: 'done', finished_at: now }); await nextTick()
  expect(clear).toHaveBeenCalledOnce(); clear.mockRestore()
  await vi.advanceTimersByTimeAsync(30000)
  expect(node.querySelector('[data-training-elapsed]')?.textContent).toBe('10:00')
  expect(node.querySelector('[data-training-eta]')?.textContent).toBe('—')
})
