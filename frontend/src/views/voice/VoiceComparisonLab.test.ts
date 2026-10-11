// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import VoiceComparisonLab from './VoiceComparisonLab.vue'
import * as api from '../../api/voices'
import en from '../../locales/en'
import { comparison, voiceProfile, preparedVoice } from './voiceTestFixtures'
import type { VoiceComparisonResponse, VoiceTrialRating } from '../../api/contracts'

vi.mock('../../api/voices', async original => ({ ...await original<typeof import('../../api/voices')>(), listVoiceComparisons: vi.fn(), listVoiceTrialSources: vi.fn(), voiceSeparationOptions: vi.fn(), rateVoiceTrial: vi.fn() }))
vi.mock('../../components/shared/WaveformPlayer.vue', () => ({ default: { render: () => null } }))
let app: App | undefined
let saved: VoiceTrialRating
function ratedComparison(): VoiceComparisonResponse {
  const complete = comparison(), trial = complete.trials[0]
  if (!trial) throw new Error('Missing trial')
  trial.rating = { ...saved }
  return complete
}
async function settle() { for (let index = 0; index < 12; index++) await nextTick() }
beforeEach(() => {
  vi.useFakeTimers()
  saved = { identity: 2, pitch: 2, intelligibility: 2, artifacts: 2, notes: 'Saved note' }
  vi.mocked(api.listVoiceTrialSources).mockResolvedValue([])
  vi.mocked(api.voiceSeparationOptions).mockResolvedValue([{ id: 'fast', available: true }])
  vi.mocked(api.listVoiceComparisons).mockImplementation(async () => [ratedComparison(), { ...comparison(), id: 'comparison-2', status: 'running', trials: [] }])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })

it('preserves unfinished ratings through polling and hydrates saved ratings after an explicit save', async () => {
  app = createApp({ render: () => h(VoiceComparisonLab, { voice: voiceProfile(), preparation: preparedVoice(), active: true }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const root = document.body.appendChild(document.createElement('div')); app.mount(root); await settle()
  const rating = root.querySelector<HTMLSelectElement>('select[aria-label="Singer identity"]'), notes = root.querySelector<HTMLTextAreaElement>('textarea')
  if (!rating || !notes) throw new Error('Missing rating inputs')
  rating.value = '4'; rating.dispatchEvent(new Event('change', { bubbles: true }))
  notes.value = 'Unsaved newer note'; notes.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  await vi.advanceTimersByTimeAsync(2000); await settle()
  expect(api.listVoiceComparisons).toHaveBeenCalledTimes(2)
  expect(rating.value).toBe('4'); expect(notes.value).toBe('Unsaved newer note')
  vi.mocked(api.rateVoiceTrial).mockImplementation(async (_voice, _comparison, _trial, request) => { saved = request; return ratedComparison() })
  const save = [...root.querySelectorAll('button')].find(button => button.textContent?.trim() === 'Save listening ratings')
  if (!save) throw new Error('Missing rating save')
  save.click(); await settle()
  expect(api.rateVoiceTrial).toHaveBeenCalledWith(voiceProfile().id, comparison().id, 'trial-1', { identity: 4, pitch: 2, intelligibility: 2, artifacts: 2, notes: 'Unsaved newer note' }, expect.any(AbortSignal))
  saved = { ...saved, identity: 5, notes: 'New saved note' }
  await vi.advanceTimersByTimeAsync(2000); await settle()
  expect(rating.value).toBe('5'); expect(notes.value).toBe('New saved note')
})
