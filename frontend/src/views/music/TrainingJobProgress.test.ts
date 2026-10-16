// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, type App } from 'vue'
import { i18n } from '../../i18n'
import { parseStableLoraTrainingJob, type MusicLoraTrainingJob, type StableLoraTrainingJob } from '../../api/generated'
import TrainingJobProgress from './TrainingJobProgress.vue'

let app: App | undefined
const now = '2026-10-15T21:10:00Z'
function job(overrides: Record<string, unknown> = {}) {
  return parseStableLoraTrainingJob({ id: 'c'.repeat(32), status: 'running', stage: 'training', created_at: '2026-10-15T21:00:00Z', started_at: '2026-10-15T21:00:05Z', request: { name: 'Piano', variant: 'medium', samples: [{ track_id: 42, caption: 'Piano' }], max_steps: 1000, rank: 32, learning_rate: 0.0001, seed: 42 }, progress: { stage: 'training', step: 100, total_steps: 1000, loss: 0.42, seconds_per_step: 2, updated_at: now }, ...overrides })
}
async function mount(value: StableLoraTrainingJob | MusicLoraTrainingJob = job()) {
  vi.useFakeTimers(); vi.setSystemTime(now)
  const props = reactive({ job: value, reconnecting: false })
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TrainingJobProgress, props) }).use(i18n); app.mount(node); await nextTick()
  return { node, props }
}
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
function pairedJob(): MusicLoraTrainingJob {
  const ar = { id: 'a'.repeat(32), engine: 'yue2', target: 'ar', name: 'Power', sha256: 'e'.repeat(64), created_at: now } as const
  const nar = { ...ar, id: 'b'.repeat(32), target: 'nar', sha256: 'f'.repeat(64) } as const
  return { id: 'c'.repeat(32), status: 'done', created_at: now, finished_at: now,
    request: { engine: 'yue2', name: 'Power', samples: [{ track_id: 42, caption: 'House' }], max_steps: 1000, yue_pair: { planner_steps: 500 } },
    progress: { stage: 'done', target: 'nar', step: 1000, total_steps: 1000 }, adapter: nar, adapters: [ar, nar] }
}
it('identifies both saved adapters when a paired run ends on NAR', async () => {
  const { node } = await mount(pairedJob())
  expect(node.querySelector('[data-training-run-summary]')?.textContent).toContain('AR + NAR')
  expect(node.querySelector('[data-training-stage]')?.textContent).toBe('AR + NAR adapters saved')
  expect(node.textContent).toContain('2 / 2 adapters saved')
  expect(node.querySelector('[data-yue-saved="ar"]')?.textContent).toContain('Planner (AR)')
  expect(node.querySelector('[data-yue-saved="ar"]')?.textContent).toContain('500 training steps')
  expect(node.querySelector('[data-yue-saved="nar"]')?.textContent).toContain('Acoustic (NAR)')
  expect(node.querySelector('[data-yue-saved="nar"]')?.textContent).toContain('1000 training steps')
  expect(node.querySelector('progress')?.getAttribute('value')).toBe('2')
  expect(node.querySelector('progress')?.getAttribute('max')).toBe('2')
  expect(node.querySelector('[data-training-stage]')?.textContent).not.toBe('Complete · Acoustic (NAR)')
})
it('does not claim both outputs exist just because the paired request or final NAR exists', async () => {
  const value = pairedJob(); value.adapters = value.adapter ? [value.adapter] : []
  const { node } = await mount(value)
  expect(node.textContent).not.toContain('2 / 2 adapters saved')
  expect(node.querySelector('[data-yue-saved="ar"]')).toBeNull()
})
it('keeps an AR-only completion distinct from a newly trained pair', async () => {
  const value = pairedJob(); value.request.yue_pair = null; value.request.yue_ar_source_job_id = 'd'.repeat(32)
  value.progress = { stage: 'done', target: 'ar', step: 1000, total_steps: 1000 }
  const { node } = await mount(value)
  expect(node.textContent).toContain('AR only')
  expect(node.textContent).toContain('NAR reused')
  expect(node.textContent).not.toContain('2 / 2 adapters saved')
})
it('does not advertise a prepared NVIDIA package as a trained pair', async () => {
  const value = pairedJob(); value.kind = 'nvidia-package'
  const { node } = await mount(value)
  expect(node.textContent).not.toContain('2 / 2 adapters saved')
  expect(node.querySelector('[data-yue-saved="ar"]')).toBeNull()
  expect(node.querySelector('progress')).toBeNull()
})
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

it('reports planner and acoustic phases with their own totals and measured ETA after reset', async () => {
  const value = { id: 'c'.repeat(32), status: 'running', created_at: '2026-10-15T21:00:00Z', request: { engine: 'yue2', name: 'Folk', samples: [{ track_id: 42, caption: 'Folk' }], max_steps: 1000, yue_pair: { planner_steps: 100, planner_learning_rate: 0.00005 } }, progress: { stage: 'training', target: 'ar', step: 20, total_steps: 100, seconds_per_step: 2, updated_at: now } } as const
  const { node, props } = await mount({ ...value, request: { ...value.request, samples: [...value.request.samples] } })
  expect(node.textContent).toContain('Planner (AR)')
  expect(node.textContent).toContain('20 / 100')
  expect(node.querySelector('progress')?.getAttribute('max')).toBe('100')
  expect(node.querySelector('[data-training-eta]')?.textContent).toBe('~2:40')
  expect(node.textContent).toContain('Current phase remaining')
  expect(node.textContent).toContain('does not estimate the next phase')
  props.job = { ...value, request: { ...value.request, samples: [...value.request.samples] }, progress: { ...value.progress, target: 'nar', step: 0, total_steps: 1000, seconds_per_step: null } }; await nextTick()
  expect(node.textContent).toContain('Acoustic (NAR)')
  expect(node.textContent).toContain('0 / 1000')
  expect(node.querySelector('[data-training-eta]')?.textContent).toContain('Measuring first steps')
  expect(node.textContent).not.toContain('2.0 s / step')
})
it('shows score extraction without inventing a training ETA', async () => {
  const { node } = await mount({ id: 'c'.repeat(32), status: 'running', created_at: now, request: { engine: 'yue2', name: 'Folk', samples: [{ track_id: 42, caption: 'Folk' }], yue_pair: { planner_steps: 100, planner_learning_rate: 0.00005 } }, progress: { stage: 'transcribing', target: 'ar', total_steps: 100, step: 0, updated_at: now } })
  expect(node.textContent).toContain('Extracting musical scores')
  expect(node.querySelector('[data-training-eta]')?.textContent).toBe('Available during training')
})
it('uses the AR-only request total before telemetry and identifies the preserved acoustic stage', async () => {
  const value = { id: 'd'.repeat(32), status: 'queued', created_at: now, request: { engine: 'yue2', name: 'Folk', samples: [{ track_id: 42, caption: 'Folk' }], max_steps: 120, learning_rate: 0.00004, yue_ar_source_job_id: 'c'.repeat(32) }, progress: { stage: 'preparing', target: 'ar', step: 0 } } as const
  const { node } = await mount({ ...value, request: { ...value.request, samples: [...value.request.samples] } })
  expect(node.textContent).toContain('0 / 120')
  expect(node.textContent).toContain('AR only')
  expect(node.textContent).toContain('NAR reused')
  expect(node.textContent).toContain('Track #42')
  expect(node.textContent).not.toContain('0 / 1000')
})
