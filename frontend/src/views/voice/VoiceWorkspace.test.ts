// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import VoiceClonePage from './VoiceClonePage.vue'
import * as api from '../../api/voices'
import { i18n, setLocale } from '../../i18n'
import { preparedVoice, voiceProfile } from './voiceTestFixtures'

vi.mock('../../api/voices', async (original) => ({ ...await original<typeof import('../../api/voices')>(),
  listVoices: vi.fn(), getVoicePreparation: vi.fn(), prepareVoice: vi.fn(), selectVoiceSamples: vi.fn(),
  voiceSeparationOptions: vi.fn(), listVoiceTrialSources: vi.fn(), listVoiceComparisons: vi.fn(),
  analyzeVoiceCoverage: vi.fn(), startVoiceComparison: vi.fn(), cancelVoicePreparation: vi.fn(),
}))
vi.mock('../../composables/audioPlayback', async (original) => ({ ...await original<typeof import('../../composables/audioPlayback')>(), fetchAndComputePeaks: vi.fn().mockResolvedValue([0.1, 0.4]) }))

let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); setLocale('en')
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile()])
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.voiceSeparationOptions).mockResolvedValue([{ id: 'fast', available: true }, { id: 'high', available: true }])
  vi.mocked(api.listVoiceTrialSources).mockResolvedValue([])
  vi.mocked(api.listVoiceComparisons).mockResolvedValue([])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount() {
  app = createApp(VoiceClonePage)
  app.use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] }))
  const container = document.createElement('div'); document.body.append(container); app.mount(container); await settle(); return container
}
function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === label || item.getAttribute('aria-label') === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
async function click(container: HTMLElement, label: string) { button(container, label).click(); await settle() }
function input(container: HTMLElement, label: string) {
  const found = container.querySelector(`[aria-label="${label}"]`)
  if (!(found instanceof HTMLInputElement)) throw new Error(`Missing input: ${label}`)
  return found
}
function select(container: HTMLElement, label: string) {
  const found = container.querySelector(`[aria-label="${label}"]`)
  if (!(found instanceof HTMLSelectElement)) throw new Error(`Missing select: ${label}`)
  return found
}
async function change(element: HTMLInputElement | HTMLSelectElement, value: string) { element.value = value; element.dispatchEvent(new Event('change', { bubbles: true })); element.dispatchEvent(new Event('input', { bubbles: true })); await settle() }

it('shows numbered objectives and one persisted job summary directly below every step', async () => {
  vi.setSystemTime(new Date(50_000))
  vi.mocked(api.getVoicePreparation).mockResolvedValue({ ...preparedVoice(), status: 'running', progress: { job_id: 'prep-job', kind: 'preparation', status: 'running', queued_at: 10, started_at: 20, observed_at: 40, phase_started_at: 30, phase: 'slicing', phase_current: 2, phase_total: 10, phase_unit: 'samples', files_completed: 0, files_total: 1, current_file: 'song.wav', estimated_phase_remaining_sec: null } })
  const container = await mount()
  const summary = container.querySelector('[data-voice-job-summary]')
  expect(summary).not.toBeNull()
  expect(summary?.previousElementSibling?.getAttribute('role')).toBe('tablist')
  expect(summary?.textContent).toContain(voiceProfile().name)
  expect(summary?.textContent).toContain('2 / 10 samples completed')
  expect(summary?.textContent).toContain('song.wav')
  expect(summary?.textContent).toContain('0:40')
  expect(summary?.textContent).toContain('Estimating')
  expect(button(container, 'Files').textContent).toContain('1')
  expect(button(container, 'Build').getAttribute('data-step-state')).toBe('blocked')
  await click(container, 'Compare')
  expect(container.querySelector('[data-voice-job-summary]')).toBe(summary)
  expect(container.querySelectorAll('[data-voice-job-summary]')).toHaveLength(1)
  expect(summary?.querySelector('[data-voice-elapsed]')?.closest('[aria-live]')).toBeNull()
  vi.advanceTimersByTime(1000); await settle()
  expect(summary?.textContent).toContain('0:41')
  app?.unmount(); app = undefined
  await settle(); vi.runAllTicks(); await vi.advanceTimersByTimeAsync(10_000); await settle()
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

it('keeps a cancelled job clock fixed and offers retry while another step is open', async () => {
  vi.setSystemTime(new Date(50_000))
  vi.mocked(api.getVoicePreparation).mockResolvedValue({ ...preparedVoice(), status: 'cancelled', error_code: 'cancelled', progress: { job_id: 'prep-job', kind: 'preparation', status: 'cancelled', queued_at: 10, started_at: 20, finished_at: 30, observed_at: 25, phase: 'separating' } })
  const container = await mount()
  await click(container, 'Coverage')
  const summary = container.querySelector('[data-voice-job-summary]')
  expect(summary?.textContent).toContain('0:20')
  expect(summary?.textContent).toContain('Retry preparation')
  vi.advanceTimersByTime(1000); await settle()
  expect(summary?.textContent).toContain('0:20')
})

it('offers coverage-only retry after automatic analysis cancellation', async () => {
  vi.mocked(api.getVoicePreparation).mockResolvedValue({ ...preparedVoice(), operation: 'coverage', warnings: ['coverage_cancelled'], progress: { job_id: 'prep-job', kind: 'preparation', status: 'cancelled', queued_at: 10, finished_at: 20, observed_at: 19, phase: 'coverage' } })
  vi.mocked(api.analyzeVoiceCoverage).mockResolvedValue({ ...preparedVoice(), operation: 'coverage', status: 'queued' })
  const container = await mount()
  await click(container, 'Build'); await click(container, 'Retry coverage analysis')
  expect(api.analyzeVoiceCoverage).toHaveBeenCalledTimes(1)
  expect(api.prepareVoice).not.toHaveBeenCalled()
})

it('shows invalidated preparation even when an older published build has timing', async () => {
  vi.mocked(api.listVoices).mockResolvedValue([{ ...voiceProfile(), status: 'ready', usable: true, job_progress: { job_id: 'build-job', kind: 'build', status: 'done', queued_at: 10, finished_at: 20, observed_at: 19, phase: 'publishing' } }])
  vi.mocked(api.getVoicePreparation).mockResolvedValue({ ...preparedVoice(), operation: 'coverage', status: 'failed', error_code: 'source_changed' })
  const container = await mount()
  await click(container, 'Compare')
  const summary = container.querySelector('[data-voice-job-summary]')
  expect(summary?.textContent).toContain('Audio preparation · failed')
  expect(summary?.textContent).toContain(api.voiceErrorText('source_changed'))
  expect(summary?.textContent).toContain('Retry preparation')
})

it('invalidates completed build guidance after editing a saved selection', async () => {
  vi.mocked(api.listVoices).mockResolvedValue([{ ...voiceProfile(), status: 'ready', usable: true, job_progress: { job_id: 'build-job', kind: 'build', status: 'done', preparation_revision: 'revision-1', queued_at: 10, started_at: 11, finished_at: 20, observed_at: 19, phase: 'publishing' } }])
  const container = await mount()
  expect(button(container, 'Build').getAttribute('data-step-state')).toBe('complete')
  await click(container, 'Samples')
  input(container, 'Include segment: segment-1').click(); await settle()
  expect(button(container, 'Build').getAttribute('data-step-state')).toBe('blocked')
  expect(button(container, 'Coverage').getAttribute('data-step-state')).toBe('blocked')
  expect(button(container, 'Compare').getAttribute('data-step-state')).toBe('blocked')
  expect(button(container, 'Next: Coverage').disabled).toBe(true)
  // Publication remains usable, but it is not completion of the unsaved draft.
  expect(button(container, 'Use for new songs').disabled).toBe(false)
})

it('ignores a global cancel response after switching voices', async () => {
  const first = voiceProfile()
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Second voice' }
  vi.mocked(api.listVoices).mockResolvedValue([first, second])
  vi.mocked(api.getVoicePreparation).mockImplementation(async (id) => ({ ...preparedVoice(), status: id === first.id ? 'running' : 'done', progress: id === first.id ? { job_id: 'prep-first', kind: 'preparation', status: 'running', queued_at: 10, observed_at: 11, phase: 'separating' } : null }))
  let resolve: (response: ReturnType<typeof preparedVoice>) => void = () => { throw new Error('Missing deferred cancellation') }
  vi.mocked(api.cancelVoicePreparation).mockReturnValue(new Promise((done) => { resolve = done }))
  const container = await mount()
  await click(container, 'Compare'); await click(container, 'Cancel')
  const secondButton = [...container.querySelectorAll('button')].find((item) => item.textContent?.includes('Second voice'))
  if (!secondButton) throw new Error('Missing second voice')
  secondButton.click(); await settle()
  resolve({ ...preparedVoice(), status: 'cancelled', error_code: 'cancelled' }); await settle()
  expect(container.querySelector('h2')?.textContent).toBe('Second voice')
  expect(container.querySelector('[data-voice-job-summary]')?.textContent).toContain('Audio preparation · done')
  await vi.advanceTimersByTimeAsync(10_000); await settle()
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(2)
})

it('provides keyboard tabs and guided next without restarting preparation or losing drafts', async () => {
  const container = await mount()
  expect(container.querySelectorAll('[role="tab"]')).toHaveLength(5)
  for (const tab of container.querySelectorAll('[role="tab"]')) expect(document.getElementById(tab.getAttribute('aria-controls') ?? '')).not.toBeNull()
  await click(container, 'Next: Samples')
  const checkbox = input(container, 'Include segment: segment-1'); checkbox.click(); await settle()
  await click(container, 'Files'); await click(container, 'Samples')
  expect(input(container, 'Include segment: segment-1').checked).toBe(false)
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(1)
  const samplesTab = button(container, 'Samples')
  samplesTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await settle()
  expect(button(container, 'Coverage').getAttribute('aria-selected')).toBe('true')
  expect(document.activeElement).toBe(button(container, 'Coverage'))
})

it('limits sample bulk actions to visible accepted samples and keeps cleanup available only where prepared', async () => {
  const preparation = preparedVoice()
  const accepted = preparation.segments?.[0]
  if (!accepted) throw new Error('Missing segment fixture')
  preparation.segments?.push({ ...accepted, id: 'segment-3', source_filename: 'other.wav', has_cleaned: false })
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparation)
  const container = await mount()
  await click(container, 'Samples')
  await click(container, 'Clear visible selection')
  expect(input(container, 'Include segment: segment-1').checked).toBe(false)
  await change(input(container, 'Search samples'), 'other.wav')
  await click(container, 'Select visible accepted')
  expect(input(container, 'Include segment: segment-3').checked).toBe(true)
  expect(container.querySelector('[aria-label="Use cleaned segment: segment-3"]')).toBeNull()
  await change(input(container, 'Search samples'), '')
  expect(input(container, 'Include segment: segment-1').checked).toBe(false)
  expect(input(container, 'Include segment: segment-2').disabled).toBe(true)
})

it('passes the reviewed duration budget with custom bounded minutes', async () => {
  vi.mocked(api.getVoicePreparation).mockResolvedValue({ status: 'idle' })
  vi.mocked(api.prepareVoice).mockResolvedValue({ status: 'queued' })
  const container = await mount()
  input(container, 'These sources contain the same intended singer').click(); await settle()
  await change(select(container, 'Preparation duration'), '30')
  await click(container, 'Prepare audio')
  expect(api.prepareVoice).toHaveBeenCalledWith(voiceProfile().id, expect.objectContaining({ max_selected_seconds: 1800 }), expect.any(AbortSignal))
})

it('lets processed samples play with the actual shared player during an active build', async () => {
  vi.mocked(api.listVoices).mockResolvedValue([{ ...voiceProfile(), status: 'training' }])
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
  const container = await mount()
  await click(container, 'Samples')
  const playButton = container.querySelector('[aria-label="Play"]')
  if (!(playButton instanceof HTMLButtonElement)) throw new Error('Missing actual player control')
  expect(playButton.matches(':disabled')).toBe(false)
  expect(input(container, 'Include segment: segment-1').disabled).toBe(true)
  playButton.click(); await settle()
  expect(play).toHaveBeenCalledTimes(1)
})

it('keeps file bulk edits scoped to the search and invalidates singer confirmation', async () => {
  const profile = voiceProfile()
  profile.recordings.push({ filename: 'other.wav', bytes: 5000 })
  vi.mocked(api.listVoices).mockResolvedValue([profile])
  const container = await mount()
  await change(input(container, 'Search files'), 'other')
  await click(container, 'Exclude visible files')
  await change(input(container, 'Search files'), '')
  const sources = [...container.querySelectorAll('label')].filter((label) => label.querySelector('input[type=checkbox]') && ['other.wav', 'song.wav'].includes(label.textContent?.trim() ?? ''))
  const song = sources.find((label) => label.textContent?.trim() === 'song.wav')?.querySelector('input')
  const other = sources.find((label) => label.textContent?.trim() === 'other.wav')?.querySelector('input')
  expect(song?.checked).toBe(true)
  expect(other?.checked).toBe(false)
  expect(input(container, 'These sources contain the same intended singer').checked).toBe(false)
})

it('pages sample bulk selection and does not alter accepted rows on another page', async () => {
  const preparation = preparedVoice()
  const segment = preparation.segments?.[0]
  if (!segment) throw new Error('Missing segment fixture')
  preparation.segments = Array.from({ length: 26 }, (_, index) => ({ ...segment, id: `sample-${index}`, start_sec: index * 10, end_sec: index * 10 + 10 }))
  preparation.selected_segment_ids = []
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparation)
  const container = await mount()
  await click(container, 'Samples')
  expect(container.querySelectorAll('[aria-label^="Include segment:"]')).toHaveLength(25)
  await click(container, 'Next page'); await click(container, 'Select visible accepted')
  expect(input(container, 'Include segment: sample-25').checked).toBe(true)
  await click(container, 'Previous page')
  expect(input(container, 'Include segment: sample-0').checked).toBe(false)
  expect(container.textContent).toContain('1 selected')
})

it('requires saving draft selection before coverage and current-reference comparison', async () => {
  vi.mocked(api.listVoiceTrialSources).mockResolvedValue([{ id: 'abcdef1234567890abcdef1234567890', filename: 'held-out.wav', duration_sec: 20 }])
  const container = await mount()
  await click(container, 'Samples')
  input(container, 'Use cleaned segment: segment-1').click(); await settle()
  await click(container, 'Coverage')
  expect(button(container, 'Analyze selected samples').disabled).toBe(true)
  expect(container.textContent).toContain('Save the sample selection')
  await click(container, 'Build'); expect(button(container, 'Build voice').disabled).toBe(true)
  await click(container, 'Compare')
  expect(button(container, 'Run comparison').disabled).toBe(false)
  const reference = [...container.querySelectorAll('input')].find((item) => item.type === 'checkbox' && item.value === 'reference-1')
  if (!reference) throw new Error('Missing reference checkbox')
  reference.click(); await settle()
  expect(button(container, 'Run comparison').disabled).toBe(true)
})

it('shows measured saved coverage and treats unobserved notes as absent from the recording', async () => {
  const preparation = preparedVoice()
  preparation.coverage = { duration_sec: 10, analyzed_sec: 10, voiced_sec: 8, reliable_voiced_sec: 6, pitch_p05_hz: 220, pitch_median_hz: 230, pitch_p95_hz: 247, pitch_bins: [{ midi_note: 57, seconds: 3 }, { midi_note: 59, seconds: 3 }], measurements: 1, unavailable_segments: 0, spectral_rolloff95_hz: 8000, high_band_energy_fraction: 0.2, warnings: ['observed_pitch_is_not_full_vocal_range'] }
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparation)
  const container = await mount()
  await click(container, 'Coverage')
  expect(container.textContent).toContain('Central 90% span: 220 Hz–247 Hz')
  expect(container.textContent).toContain('A♯3: not observed')
  expect(container.textContent).toContain('Observed notes are not the singer’s full vocal range')
  expect(container.textContent).toContain('8000 Hz')
  expect(container.textContent).not.toContain('Identity score')
})

it('uses the existing preparation loop for coverage and ignores a response after teardown', async () => {
  let release: ((response: ReturnType<typeof preparedVoice>) => void) | undefined
  vi.mocked(api.analyzeVoiceCoverage).mockReturnValue(new Promise((resolve) => { release = resolve }))
  const container = await mount()
  await click(container, 'Coverage'); await click(container, 'Analyze selected samples')
  expect(api.analyzeVoiceCoverage).toHaveBeenCalledWith(voiceProfile().id, 'revision-1', expect.any(AbortSignal))
  const signal = vi.mocked(api.analyzeVoiceCoverage).mock.calls[0]?.[2]
  await click(container, 'Files'); await click(container, 'Coverage')
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(1)
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  if (!release) throw new Error('Missing coverage resolver')
  release({ ...preparedVoice(), operation: 'coverage', status: 'running' })
  await settle(); await vi.advanceTimersByTimeAsync(10_000)
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(1)
})

it('excludes legacy accepted clips outside the training duration from bulk selection', async () => {
  const preparation = preparedVoice()
  const segment = preparation.segments?.[0]
  if (!segment) throw new Error('Missing sample fixture')
  preparation.segments?.push({ ...segment, id: 'legacy-short', duration_sec: 0.8, start_sec: 0, end_sec: 0.8 }, { ...segment, id: 'legacy-long', duration_sec: 31, start_sec: 20, end_sec: 51 })
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparation)
  const container = await mount()
  await click(container, 'Samples'); await click(container, 'Select visible accepted')
  expect(input(container, 'Include segment: legacy-short').disabled).toBe(true)
  expect(input(container, 'Include segment: legacy-short').checked).toBe(false)
  expect(input(container, 'Include segment: legacy-long').checked).toBe(false)
  expect(container.textContent).toContain('outside the supported 1–30 second')
})

it('keeps the saved selection buildable when optional coverage analysis is cancelled', async () => {
  const preparation = preparedVoice()
  vi.mocked(api.analyzeVoiceCoverage).mockResolvedValue({ ...preparation, status: 'running', operation: 'coverage' })
  vi.mocked(api.cancelVoicePreparation).mockResolvedValue({ ...preparation, operation: 'coverage', warnings: ['coverage_cancelled'] })
  const container = await mount()
  await click(container, 'Coverage'); await click(container, 'Analyze selected samples'); await click(container, 'Cancel')
  expect(container.textContent).toContain('Coverage analysis was cancelled')
  expect(button(container, 'Analyze selected samples').disabled).toBe(false)
  await click(container, 'Build')
  expect(button(container, 'Build voice').disabled).toBe(false)
})

it('reports selected duration separately from measured coverage when samples are unavailable', async () => {
  const preparation = preparedVoice()
  preparation.accepted_seconds = 120
  preparation.coverage = { duration_sec: 60, analyzed_sec: 50, voiced_sec: 20, reliable_voiced_sec: 10, measurements: 1, unavailable_segments: 1 }
  preparation.selected_segment_ids = ['segment-1', 'not-measured']
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparation)
  const container = await mount()
  await click(container, 'Coverage')
  const selected = [...container.querySelectorAll('dt')].find((item) => item.textContent === 'Selected audio')
  expect(selected?.parentElement?.textContent).toContain('2.0 min')
  expect(container.textContent).toContain('1 of 2 selected samples measured · 1 samples unavailable')
})

it('saves a changed duration budget with the existing samples without repeating preparation', async () => {
  const prepared = preparedVoice()
  vi.mocked(api.selectVoiceSamples).mockResolvedValue({ ...prepared, revision: 'revision-2', options: { ...prepared.options, max_selected_seconds: 1800 } })
  const container = await mount()
  await change(select(container, 'Preparation duration'), '30')
  expect(container.textContent).toContain('Save the new duration budget in Samples')
  await click(container, 'Coverage')
  expect(button(container, 'Analyze selected samples').disabled).toBe(true)
  await click(container, 'Build')
  expect(button(container, 'Build voice').disabled).toBe(true)
  await click(container, 'Samples')
  expect(button(container, 'Save selection').disabled).toBe(false)
  await click(container, 'Save selection')
  expect(api.selectVoiceSamples).toHaveBeenCalledWith(voiceProfile().id, { revision: 'revision-1', segment_ids: ['segment-1'], reference_id: 'reference-1', cleaned_segment_ids: [], max_selected_seconds: 1800 }, expect.any(AbortSignal))
  expect(api.prepareVoice).not.toHaveBeenCalled()
  await click(container, 'Build')
  expect(button(container, 'Build voice').disabled).toBe(false)
})

it('explains an over-budget selection and keeps saving disabled until it fits', async () => {
  const prepared = preparedVoice()
  const segment = prepared.segments?.[0]
  if (!segment) throw new Error('Missing sample fixture')
  prepared.segments = Array.from({ length: 7 }, (_, index) => ({ ...segment, id: `long-${index}`, start_sec: index * 10, end_sec: index * 10 + 10 }))
  prepared.selected_segment_ids = prepared.segments.map((item) => item.id)
  prepared.references = [{ id: 'reference-1', segment_id: 'long-0', source_filename: segment.source_filename, start_sec: 4, end_sec: 14, duration_sec: 10, score: 0.8 }]
  vi.mocked(api.getVoicePreparation).mockResolvedValue(prepared)
  const container = await mount()
  await change(select(container, 'Preparation duration'), 'custom')
  await change(input(container, 'Custom minutes (1–60)'), '1')
  await click(container, 'Samples')
  expect(container.textContent).toContain('Selection exceeds the 1.0 minute budget')
  expect(button(container, 'Save selection').disabled).toBe(true)
  input(container, 'Include segment: long-6').click(); await settle()
  expect(button(container, 'Save selection').disabled).toBe(false)
})
