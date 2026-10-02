// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import VoiceClonePage from './VoiceClonePage.vue'
import * as api from '../../api/voices'
import { i18n, setLocale } from '../../i18n'
import { preparedVoice, voiceProfile, comparison } from './voiceTestFixtures'
import type { VoicePreparationResponse, VoiceProfileResponse, VoiceUploadResponse } from '../../api/contracts'
import { ApiError } from '../../api/http'

vi.mock('../../api/voices', async (original) => ({ ...await original<typeof import('../../api/voices')>(),
  listVoices: vi.fn(), getVoicePreparation: vi.fn(), prepareVoice: vi.fn(), cancelVoicePreparation: vi.fn(),
  selectVoiceSamples: vi.fn(), buildVoice: vi.fn(), selectVoiceModel: vi.fn(), cancelVoiceBuild: vi.fn(),
  voiceSeparationOptions: vi.fn(), listVoiceTrialSources: vi.fn(), listVoiceComparisons: vi.fn(),
  startVoiceComparison: vi.fn(), cancelVoiceComparison: vi.fn(), uploadVoiceTrialSource: vi.fn(), rateVoiceTrial: vi.fn(),
  uploadRecordings: vi.fn(),
  createVoice: vi.fn(), deleteVoice: vi.fn(),
}))
vi.mock('../../components/shared/WaveformPlayer.vue', () => ({ default: { props: ['src'], setup: (props: { src: string }) => () => h('audio', { src: props.src, controls: true }) } }))

let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  setLocale('en')
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile()])
  vi.mocked(api.getVoicePreparation).mockResolvedValue({ status: 'idle' })
  vi.mocked(api.voiceSeparationOptions).mockResolvedValue([{ id: 'fast', available: true }, { id: 'high', available: true }, { id: 'roformer', available: false, reason: 'separation_unavailable' }])
  vi.mocked(api.listVoiceTrialSources).mockResolvedValue([{ id: 'abcdef1234567890abcdef1234567890', filename: 'held-out.wav', duration_sec: 20 }])
  vi.mocked(api.listVoiceComparisons).mockResolvedValue([])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { await nextTick(); await nextTick(); await nextTick(); await nextTick() }
async function mount() {
  app = createApp(VoiceClonePage)
  app.use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] }))
  const container = document.createElement('div')
  document.body.append(container)
  app.mount(container)
  await settle()
  return container
}
function button(container: HTMLElement, text: string) {
  const found = [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === text || item.getAttribute('aria-label') === text)
  if (!found) throw new Error(`Missing button: ${text}`)
  return found
}
async function click(container: HTMLElement, text: string) { button(container, text).click(); await settle() }
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
async function change(element: HTMLInputElement | HTMLSelectElement, value: string) {
  element.value = value
  element.dispatchEvent(new Event('change', { bubbles: true }))
  element.dispatchEvent(new Event('input', { bubbles: true }))
  await settle()
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>((release) => { resolve = release })
  return { promise, resolve }
}

it('requires singer confirmation and preparation before building', async () => {
  const container = await mount()
  expect(container.textContent).toContain('Prepare and review')
  expect(button(container, 'Prepare audio').disabled).toBe(true)
  await click(container, 'Build')
  expect(button(container, 'Build voice').disabled).toBe(true)
  await click(container, 'Files')
  const confirmation = input(container, 'These sources contain the same intended singer')
  confirmation.click()
  await change(select(container, 'Input type: song.wav'), 'vocal')
  await change(select(container, 'Separation quality'), 'high')
  vi.mocked(api.prepareVoice).mockResolvedValue({ status: 'queued' })
  await click(container, 'Prepare audio')
  expect(api.prepareVoice).toHaveBeenCalledWith(voiceProfile().id, {
    sources: [{ filename: 'song.wav', enabled: true, kind: 'vocal' }], separation_quality: 'high', clean: false, singer_confirmed: true, max_selected_seconds: 900,
  }, expect.any(AbortSignal))
  expect(api.buildVoice).not.toHaveBeenCalled()
})

it('shows original boundaries and dry/cleaned audio and sends an explicit reviewed selection', async () => {
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.selectVoiceSamples).mockResolvedValue({ ...preparedVoice(), revision: 'revision-2', cleaned_segment_ids: ['segment-1'] })
  const container = await mount()
  await click(container, 'Samples')
  expect(container.textContent).toContain('4.0–14.0 s')
  expect(container.textContent).toContain('Automatically rejected')
  expect([...container.querySelectorAll('audio')].map((audio) => audio.getAttribute('src'))).toContain(`/api/voices/${voiceProfile().id}/samples/segment-1?variant=cleaned&revision=revision-1`)
  input(container, 'Use cleaned segment: segment-1').click()
  await settle()
  await click(container, 'Save selection')
  expect([...container.querySelectorAll('audio')].map((audio) => audio.getAttribute('src'))).toContain(`/api/voices/${voiceProfile().id}/reference/reference-1?revision=revision-2`)
  expect(api.selectVoiceSamples).toHaveBeenCalledWith(voiceProfile().id, { revision: 'revision-1', segment_ids: ['segment-1'], reference_id: 'reference-1', cleaned_segment_ids: ['segment-1'], max_selected_seconds: 900 }, expect.any(AbortSignal))
  vi.mocked(api.buildVoice).mockResolvedValue({ ...voiceProfile(), status: 'queued' })
  await click(container, 'Build')
  await change(select(container, 'Training mode'), '500')
  await click(container, 'Build voice')
  expect(api.buildVoice).toHaveBeenCalledWith(voiceProfile().id, { preparation_revision: 'revision-2', training_steps: 500, resume: false, compare_checkpoints: false }, expect.any(AbortSignal))
})

it('does not expose a late preparation response after switching voices', async () => {
  const request = deferred<VoicePreparationResponse>()
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other voice' }
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile(), second])
  vi.mocked(api.getVoicePreparation).mockReturnValueOnce(request.promise).mockResolvedValue({ status: 'idle' })
  const container = await mount()
  const other = [...container.querySelectorAll('button')].find((item) => item.textContent?.includes('Other voice'))
  if (!other) throw new Error('Missing other voice')
  other.click()
  await settle()
  const signal = vi.mocked(api.getVoicePreparation).mock.calls[0]?.[1]
  expect(signal?.aborted).toBe(true)
  request.resolve(preparedVoice())
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(container.textContent).not.toContain('4.0–14.0 s')
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(2)
})

it('does not resume voice-list polling when a build finishes after unmount', async () => {
  const request = deferred<VoiceProfileResponse>()
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.buildVoice).mockReturnValue(request.promise)
  const container = await mount()
  await click(container, 'Build')
  await click(container, 'Build voice')
  app?.unmount(); app = undefined
  request.resolve({ ...voiceProfile(), status: 'queued' })
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(api.listVoices).toHaveBeenCalledTimes(1)
})

it('runs held-out comparisons with explicit model/reference/steps and separate human ratings', async () => {
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.startVoiceComparison).mockResolvedValue(comparison())
  vi.mocked(api.rateVoiceTrial).mockResolvedValue(comparison())
  const container = await mount()
  await click(container, 'Compare')
  expect(container.textContent).toContain('Held-out listening comparison')
  await click(container, 'Compare')
  await click(container, 'Run comparison')
  expect(api.startVoiceComparison).toHaveBeenCalledWith(voiceProfile().id, expect.objectContaining({
    source_id: 'abcdef1234567890abcdef1234567890', model_ids: ['base'], reference_ids: [], diffusion_steps: [30], seed: 42,
  }), expect.any(AbortSignal))
  expect(container.textContent).toContain('10.1 s')
  expect(container.textContent).toContain('-18.0 dB')
  expect(container.textContent).not.toContain('Identity score')
  await change(select(container, 'Singer identity'), '5')
  await change(select(container, 'Pitch accuracy'), '4')
  await change(select(container, 'Intelligibility'), '3')
  await change(select(container, 'Freedom from artifacts'), '2')
  await click(container, 'Save listening ratings')
  expect(api.rateVoiceTrial).toHaveBeenCalledWith(voiceProfile().id, 'comparison-1', 'trial-1', { identity: 5, pitch: 4, intelligibility: 3, artifacts: 2, notes: '' }, expect.any(AbortSignal))
})

it('enables resume only for a higher target and keeps checkpoint comparison fresh', async () => {
  const profile = { ...voiceProfile(), active_model_id: 'trained-200' }
  vi.mocked(api.listVoices).mockResolvedValue([profile])
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.buildVoice).mockResolvedValue(profile)
  const container = await mount()
  await click(container, 'Build')
  const resume = [...container.querySelectorAll('label')].find((label) => label.textContent?.includes('Resume the active model explicitly'))?.querySelector('input')
  if (!(resume instanceof HTMLInputElement)) throw new Error('Missing resume input')
  expect(resume.disabled).toBe(true)
  await change(select(container, 'Training mode'), '500')
  expect(resume.disabled).toBe(false)
  resume.click()
  await settle()
  await click(container, 'Build voice')
  expect(api.buildVoice).toHaveBeenLastCalledWith(profile.id, expect.objectContaining({ training_steps: 500, resume: true, compare_checkpoints: false }), expect.any(AbortSignal))
  await change(select(container, 'Training mode'), 'compare')
  expect(resume.disabled).toBe(true)
  expect(resume.checked).toBe(false)
  await click(container, 'Build voice')
  expect(api.buildVoice).toHaveBeenLastCalledWith(profile.id, expect.objectContaining({ training_steps: 1000, resume: false, compare_checkpoints: true }), expect.any(AbortSignal))
})

it('ignores a training-source upload response after the selected voice changes', async () => {
  const request = deferred<VoiceUploadResponse>()
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other voice' }
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile(), second])
  vi.mocked(api.uploadRecordings).mockReturnValue(request.promise)
  const container = await mount()
  const fileInput = container.querySelector('input[type=file]')
  if (!(fileInput instanceof HTMLInputElement)) throw new Error('Missing training input')
  const transfer = new DataTransfer()
  transfer.items.add(new File(['audio'], 'new.wav', { type: 'audio/wav' }))
  Object.defineProperty(fileInput, 'files', { value: transfer.files })
  fileInput.dispatchEvent(new Event('change', { bubbles: true }))
  await settle()
  expect(api.uploadRecordings).toHaveBeenCalledTimes(1)
  const other = [...container.querySelectorAll('button')].find((item) => item.textContent?.includes('Other voice'))
  if (!other) throw new Error('Missing other voice')
  other.click()
  await settle()
  request.resolve({ voice: voiceProfile(), saved: ['new.wav'], skipped: [] })
  await settle()
  expect(container.textContent).not.toContain('Saved 1 files')
})

it('keeps the newly created voice upload busy until its files finish saving', async () => {
  const request = deferred<VoiceUploadResponse>()
  vi.mocked(api.listVoices).mockResolvedValue([])
  vi.mocked(api.createVoice).mockResolvedValue(voiceProfile())
  vi.mocked(api.uploadRecordings).mockReturnValue(request.promise)
  const container = await mount()
  const fileInput = container.querySelector('input[type=file]')
  if (!(fileInput instanceof HTMLInputElement)) throw new Error('Missing training input')
  const transfer = new DataTransfer()
  transfer.items.add(new File(['audio'], 'new.wav', { type: 'audio/wav' }))
  Object.defineProperty(fileInput, 'files', { value: transfer.files })
  fileInput.dispatchEvent(new Event('change', { bubbles: true }))
  await settle()
  fileInput.dispatchEvent(new Event('change', { bubbles: true }))
  await settle()
  expect(api.uploadRecordings).toHaveBeenCalledTimes(1)
  request.resolve({ voice: voiceProfile(), saved: ['new.wav'], skipped: [] })
  await settle()
})

it('disables builds while training source files are being uploaded', async () => {
  const request = deferred<VoiceUploadResponse>()
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.uploadRecordings).mockReturnValue(request.promise)
  const container = await mount()
  const fileInput = container.querySelector('input[type=file]')
  if (!(fileInput instanceof HTMLInputElement)) throw new Error('Missing training input')
  const transfer = new DataTransfer()
  transfer.items.add(new File(['audio'], 'song.wav', { type: 'audio/wav' }))
  Object.defineProperty(fileInput, 'files', { value: transfer.files })
  fileInput.dispatchEvent(new Event('change', { bubbles: true }))
  await settle()
  await click(container, 'Build')
  expect(button(container, 'Build voice').disabled).toBe(true)
  request.resolve({ voice: voiceProfile(), saved: ['song.wav'], skipped: [] })
  await settle()
})

it('reloads canonical preparation after uploads and requires confirmation of added sources', async () => {
  const updated = voiceProfile()
  updated.recordings.push({ filename: 'new.wav', bytes: 100 })
  vi.mocked(api.getVoicePreparation).mockResolvedValueOnce(preparedVoice()).mockResolvedValue({ ...preparedVoice(), status: 'failed', error_code: 'source_changed' })
  vi.mocked(api.uploadRecordings).mockResolvedValue({ voice: updated, saved: ['new.wav'], skipped: [] })
  const container = await mount()
  const fileInput = container.querySelector('input[type=file]')
  if (!(fileInput instanceof HTMLInputElement)) throw new Error('Missing training input')
  const transfer = new DataTransfer()
  transfer.items.add(new File(['audio'], 'new.wav', { type: 'audio/wav' }))
  Object.defineProperty(fileInput, 'files', { value: transfer.files })
  fileInput.dispatchEvent(new Event('change', { bubbles: true }))
  await settle()
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(2)
  expect(container.textContent).toContain('Source files changed')
  expect(input(container, 'These sources contain the same intended singer').checked).toBe(false)
})

it('keeps preparation and comparison polls owned while pending and aborts them on unmount', async () => {
  const preparation = deferred<VoicePreparationResponse>()
  const comparisonList = deferred<ReturnType<typeof comparison>[]>()
  vi.mocked(api.getVoicePreparation).mockResolvedValueOnce({ ...preparedVoice(), status: 'running' }).mockReturnValue(preparation.promise)
  vi.mocked(api.listVoiceComparisons).mockResolvedValueOnce([{ ...comparison(), status: 'running' }]).mockReturnValue(comparisonList.promise)
  await mount()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(2)
  expect(api.listVoiceComparisons).toHaveBeenCalledTimes(2)
  app?.unmount(); app = undefined
  expect(vi.mocked(api.getVoicePreparation).mock.calls[1]?.[1]?.aborted).toBe(true)
  expect(vi.mocked(api.listVoiceComparisons).mock.calls[1]?.[1]?.aborted).toBe(true)
  preparation.resolve({ ...preparedVoice(), status: 'running' })
  comparisonList.resolve([{ ...comparison(), status: 'running' }])
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(api.getVoicePreparation).toHaveBeenCalledTimes(2)
  expect(api.listVoiceComparisons).toHaveBeenCalledTimes(2)
})

it('recovers stale selection with an explicit preparation reload', async () => {
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.selectVoiceSamples).mockRejectedValue(new ApiError('stale_preparation', 409))
  const container = await mount()
  await click(container, 'Samples')
  input(container, 'Use cleaned segment: segment-1').click()
  await settle()
  await click(container, 'Save selection')
  expect(container.textContent).toContain('Preparation changed')
  vi.mocked(api.getVoicePreparation).mockResolvedValue({ ...preparedVoice(), revision: 'revision-new' })
  await click(container, 'Files')
  await click(container, 'Reload preparation')
  expect(container.textContent).not.toContain('Preparation changed')
  await click(container, 'Samples')
  expect(input(container, 'Use cleaned segment: segment-1').checked).toBe(false)
  await click(container, 'Build')
  expect(button(container, 'Build voice').disabled).toBe(false)
})

it('allows isolated-vocal preparation without an installed separator', async () => {
  vi.mocked(api.voiceSeparationOptions).mockResolvedValue([{ id: 'fast', available: false, reason: 'demucs_not_installed' }])
  const container = await mount()
  input(container, 'These sources contain the same intended singer').click()
  await change(select(container, 'Input type: song.wav'), 'vocal')
  expect(button(container, 'Prepare audio').disabled).toBe(false)
})

it('cancels a comparison without restarting it when cancellation finishes after unmount', async () => {
  const request = deferred<ReturnType<typeof comparison>>()
  vi.mocked(api.startVoiceComparison).mockResolvedValue({ ...comparison(), status: 'running' })
  vi.mocked(api.cancelVoiceComparison).mockReturnValue(request.promise)
  const container = await mount()
  await click(container, 'Compare')
  await click(container, 'Run comparison')
  expect(button(container, 'Run comparison').disabled).toBe(true)
  await click(container, 'Cancel')
  expect(api.cancelVoiceComparison).toHaveBeenCalledWith(voiceProfile().id, 'comparison-1', expect.any(AbortSignal))
  app?.unmount(); app = undefined
  request.resolve({ ...comparison(), status: 'cancelled' })
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(api.listVoiceComparisons).toHaveBeenCalledTimes(1)
})

it('supports reference-only builds and selecting an available active model', async () => {
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.buildVoice).mockResolvedValue(voiceProfile())
  vi.mocked(api.selectVoiceModel).mockResolvedValue({ ...voiceProfile(), active_model_id: 'trained-200' })
  const container = await mount()
  await click(container, 'Build')
  await change(select(container, 'Training mode'), '0')
  await click(container, 'Build voice')
  expect(api.buildVoice).toHaveBeenCalledWith(voiceProfile().id, expect.objectContaining({ training_steps: 0, resume: false, compare_checkpoints: false }), expect.any(AbortSignal))
  await change(select(container, 'Active model for new songs'), 'trained-200')
  expect(api.selectVoiceModel).toHaveBeenCalledWith(voiceProfile().id, 'trained-200', expect.any(AbortSignal))
})

it('hydrates completed preparation selections when the queued revision stays the same', async () => {
  vi.mocked(api.getVoicePreparation).mockResolvedValueOnce({ ...preparedVoice(), status: 'queued', segments: [], references: [], selected_segment_ids: [], cleaned_segment_ids: [], reference_id: null }).mockResolvedValue(preparedVoice())
  const container = await mount()
  await vi.advanceTimersByTimeAsync(2000)
  await settle()
  await click(container, 'Samples')
  expect(input(container, 'Include segment: segment-1').checked).toBe(true)
  await click(container, 'Build')
  expect(button(container, 'Build voice').disabled).toBe(false)
})

it('labels trial checkpoints and references with human-readable choices and short build identities', async () => {
  const profile = voiceProfile()
  const modelId = '11111111111111111111111111111111'
  profile.models = [{ id: modelId, steps: 200, kind: 'trained' }, { id: '22222222222222222222222222222222', steps: 200, kind: 'trained' }]
  profile.active_model_id = modelId
  const result = comparison()
  const trial = result.trials[0]
  if (!trial) throw new Error('Missing trial fixture')
  trial.model_id = modelId
  vi.mocked(api.listVoices).mockResolvedValue([profile])
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.startVoiceComparison).mockResolvedValue(result)
  const container = await mount()
  await click(container, 'Compare')
  await click(container, 'Run comparison')
  const heading = container.querySelector('#trial-comparison-1-trial-1')
  expect(heading?.textContent).toContain('Trained · 200 steps')
  expect(heading?.textContent).toContain('11111111…1111')
  expect(heading?.textContent).toContain('song.wav · 4.0–14.0 s')
  expect(heading?.textContent).not.toContain(modelId)
})

it('keeps published and archived reference labels understandable when historical choices are gone', async () => {
  const result = comparison()
  const trial = result.trials[0]
  if (!trial) throw new Error('Missing trial fixture')
  trial.model_id = '11111111111111111111111111111111'
  trial.reference_id = 'published'
  result.trials.push({ ...trial, id: 'archived-trial', reference_id: '22222222222222222222222222222222' })
  vi.mocked(api.startVoiceComparison).mockResolvedValue(result)
  const container = await mount()
  await click(container, 'Compare')
  await click(container, 'Run comparison')
  expect(container.querySelector('#trial-comparison-1-trial-1')?.textContent).toContain('Published reference')
  expect(container.querySelector('#trial-comparison-1-archived-trial')?.textContent).toContain('Archived reference · 22222222…2222')
})

it('distinguishes reference-only models from different builds in every model control', async () => {
  const profile = voiceProfile()
  const modelId = '11111111111111111111111111111111'
  const otherId = '22222222222222222222222222222222'
  profile.models = [{ id: modelId, steps: 0, kind: 'base' }, { id: otherId, steps: 0, kind: 'base' }]
  profile.active_model_id = modelId
  const result = comparison()
  const trial = result.trials[0]
  if (!trial) throw new Error('Missing trial fixture')
  trial.model_id = modelId
  vi.mocked(api.listVoices).mockResolvedValue([profile])
  vi.mocked(api.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(api.startVoiceComparison).mockResolvedValue(result)
  const container = await mount()
  await click(container, 'Build')
  const modelSelect = select(container, 'Active model for new songs')
  expect(modelSelect.options[0]?.textContent).toContain('11111111…1111')
  expect(modelSelect.options[1]?.textContent).toContain('22222222…2222')
  await click(container, 'Compare')
  const checkbox = [...container.querySelectorAll('input')].find((item) => item.type === 'checkbox' && item.value === otherId)
  expect(checkbox?.closest('label')?.textContent).toContain('22222222…2222')
  await click(container, 'Compare')
  await click(container, 'Run comparison')
  expect(container.querySelector('#trial-comparison-1-trial-1')?.textContent).toContain('Reference only · 0 training steps · 11111111…1111')
})

it('keeps observing another active build after deleting the selected voice', async () => {
  const remaining: VoiceProfileResponse = { ...voiceProfile('fedcba0987654321fedcba0987654321'), name: 'Another voice', status: 'training' }
  vi.mocked(api.listVoices).mockResolvedValueOnce([voiceProfile(), remaining]).mockResolvedValue([remaining])
  vi.mocked(api.deleteVoice).mockResolvedValue(undefined)
  const container = await mount()
  await click(container, 'Delete voice')
  await vi.advanceTimersByTimeAsync(2000)
  expect(api.listVoices).toHaveBeenCalledTimes(2)
  expect(container.textContent).toContain('Another voice')
})
