// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import { audiobookReviewEn } from '../../locales/audiobookReview'
import * as api from '../../api/narrationTiming'
import NarrationDurationBudget from './NarrationDurationBudget.vue'
import type { NarrationDurationGuidance } from '../../api/contracts'
vi.mock('../../api/narrationTiming', () => ({ durationGuidance: vi.fn() }))
let app: App | undefined
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks() })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
async function settle() { for (let i = 0; i < 5; i++) await nextTick() }
function measured(): NarrationDurationGuidance { return { state: 'approximate', reason: 'measured_takes', target_seconds: 30, measurement_count: 2, measured_audio_ms: 30000, characters_per_second: 8, suggested_characters: 240, estimated_min_ms: 12000, estimated_max_ms: 18000 } }
async function mount() {
  const profile = ref('a'.repeat(32)), active = ref(true)
  app = createApp({ render: () => h(NarrationDurationBudget, { profileId: profile.value, language: 'en', text: 'Some narration text.', active: active.value }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { audiobookReview: audiobookReviewEn } } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  return { container, profile, active }
}
it('labels forecasts approximate and offers all four duration targets', async () => {
  vi.mocked(api.durationGuidance).mockResolvedValue(measured())
  const { container } = await mount(); await vi.advanceTimersByTimeAsync(350); await settle()
  expect(container.textContent).toContain('Approximate')
  expect(container.querySelectorAll('option')).toHaveLength(4)
  expect(container.textContent).toContain('240')
  expect(container.textContent).toContain('12.0–18.0')
})
it('rejects a late forecast from a previously selected voice', async () => {
  let resolve: ((value: NarrationDurationGuidance) => void) | undefined
  vi.mocked(api.durationGuidance).mockImplementationOnce(() => new Promise(done => { resolve = done })).mockResolvedValueOnce({ state: 'unavailable', reason: 'model_unverified', target_seconds: 30 })
  const { container, profile } = await mount(); await vi.advanceTimersByTimeAsync(350)
  profile.value = 'b'.repeat(32); await settle(); await vi.advanceTimersByTimeAsync(350); await settle()
  resolve?.(measured()); await settle()
  expect(container.textContent).not.toContain('240')
  expect(container.textContent).toContain('loaded model')
})
it('aborts a pending request when its workspace is hidden', async () => {
  vi.mocked(api.durationGuidance).mockImplementation(() => new Promise(() => undefined))
  const { active } = await mount(); await vi.advanceTimersByTimeAsync(350)
  const signal = vi.mocked(api.durationGuidance).mock.calls[0]?.[1]
  expect(signal?.aborted).toBe(false)
  active.value = false; await settle()
  expect(signal?.aborted).toBe(true)
})
