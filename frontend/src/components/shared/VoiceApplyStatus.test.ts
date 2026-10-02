// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import VoiceApplyStatus from './VoiceApplyStatus.vue'
import { i18n, setLocale } from '../../i18n'
import type { VoiceJobProgress } from '../../api/contracts'

const progress: VoiceJobProgress = {
  job_id: 'apply-42', kind: 'apply', status: 'running', queued_at: 100,
  started_at: 130, phase_started_at: 180, observed_at: 200, phase: 'converting',
  phase_current: 2, phase_total: 10, phase_unit: 'chunks', estimated_phase_remaining_sec: 80,
}
let app: App | undefined
let current = ref<VoiceJobProgress | null>(progress)
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(200_000); setLocale('en') })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
async function settle() { for (let i = 0; i < 5; i++) await nextTick() }
function mount(jobProgress: VoiceJobProgress | null = progress) {
  current = ref(jobProgress)
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(VoiceApplyStatus, { jobProgress: current.value, voiceName: 'Singer', phase: 'converting', startedAt: 100_000, durationSec: 240 }) }).use(i18n)
  app.mount(node)
  return node
}
it('shows the queued owner and elapsed wait without an invented finish time or measured bar', async () => {
  const node = mount({ ...progress, status: 'queued', phase: 'waiting_gpu', started_at: null, phase_current: 0, phase_total: 0, queue_reason: 'voice_training', queue_label: 'SackJo22' })
  await settle()
  expect(node.textContent).toContain('Waiting for voice training: SackJo22')
  expect(node.textContent).toContain('Elapsed: 1:40')
  expect(node.textContent).toContain('Available once this stage starts')
  expect(node.textContent).not.toContain('finishes')
  expect(node.querySelector('[role="progressbar"]')?.hasAttribute('aria-valuenow')).toBe(false)
})
it('exposes measured chunk progress and labels the estimate for this stage only', async () => {
  const node = mount(); await settle()
  expect(node.textContent).toContain('Converting voice')
  expect(node.textContent).toContain('2 / 10 audio sections')
  expect(node.textContent).toContain('Current stage remaining: ≈ 1:20')
  expect(node.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('20')
  expect(node.querySelector('[role="status"]')?.textContent).toContain('Converting voice')
})
it('withdraws a stale estimate instead of counting down indefinitely after progress stops', async () => {
  const node = mount({ ...progress, estimated_phase_remaining_sec: 30 }); await settle()
  expect(node.textContent).toContain('≈ 0:30')
  await vi.advanceTimersByTimeAsync(31_000); await settle()
  expect(node.textContent).toContain('Measuring…')
  expect(node.textContent).not.toContain('≈')
  current.value = { ...progress, observed_at: 231, phase_current: 3, estimated_phase_remaining_sec: 70 }
  await settle(); expect(node.textContent).toContain('≈ 1:10')
})
it('freezes a terminal elapsed time and resets the clock and estimate for a retry', async () => {
  const node = mount({ ...progress, status: 'failed', finished_at: 195 }); await settle()
  expect(node.textContent).toContain('Elapsed: 1:35')
  expect(vi.getTimerCount()).toBe(0)
  await vi.advanceTimersByTimeAsync(30_000); await settle()
  expect(node.textContent).toContain('Elapsed: 1:35')
  current.value = { ...progress, job_id: 'retry-42', status: 'queued', queued_at: 230, started_at: null, phase: 'queued', estimated_phase_remaining_sec: null }
  await settle(); expect(node.textContent).toContain('Elapsed: 0:00')
  expect(node.textContent).not.toContain('≈')
  app?.unmount(); app = undefined; expect(vi.getTimerCount()).toBe(0)
})
it('describes a queued cancellation as terminal instead of claiming it is still waiting', async () => {
  const node = mount({ ...progress, status: 'cancelled', phase: 'waiting_gpu', finished_at: 195, queue_reason: 'voice_training', queue_label: 'SackJo22' }); await settle()
  expect(node.textContent).toContain('Cancelled')
  expect(node.textContent).not.toContain('Waiting')
  expect(node.textContent).not.toContain('Applying Singer')
  expect(node.textContent).toContain('Elapsed: 1:35')
})
it('supports old servers without progress without guessing timing from song length', async () => {
  const node = mount(null); await settle()
  expect(node.textContent).toContain('Converting voice')
  expect(node.textContent).toContain('Measuring…')
  expect(node.textContent).not.toContain('finishes')
  expect(node.textContent).not.toContain('Elapsed:')
  expect(vi.getTimerCount()).toBe(0)
})
it('does not blame the GPU when the job is queued without a reported blocker', async () => {
  const node = mount({ ...progress, status: 'queued', phase: 'queued', phase_total: 0, queue_reason: '', queue_label: '' }); await settle()
  expect(node.textContent).toContain('Waiting to start voice conversion')
  expect(node.textContent).not.toContain('GPU')
})
it('translates queue reasons and phases in Russian', async () => {
  setLocale('ru')
  const node = mount({ ...progress, status: 'queued', phase: 'waiting_gpu', queue_reason: 'voice_conversion', queue_label: 'Singer' }); await settle()
  expect(node.textContent).toContain('Ожидание замены голоса: Singer')
  expect(node.textContent).not.toContain('voiceClone.')
  current.value = { ...progress, phase: 'analyzing' }; await settle()
  expect(node.textContent).toContain('Анализ вокала')
})
