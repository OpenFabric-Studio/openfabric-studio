// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App, type Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n, setLocale } from '../../i18n'
import * as stemsApi from '../../api/stems'
import * as midiApi from '../../api/midi'
import * as voicesApi from '../../api/voices'
import type { StemsStatus } from '../../api/stems'
import type { MidiStatus } from '../../api/midi'
import type { VoiceProfile } from '../../api/voices'
import StemsPanel from './StemsPanel.vue'
import MidiPanel from './MidiPanel.vue'
import VoiceSelect from './VoiceSelect.vue'
import VoiceClonePage from '../../views/voice/VoiceClonePage.vue'
import { preparedVoice } from '../../views/voice/voiceTestFixtures'

vi.mock('../../api/stems', () => ({ getSeparationStatus: vi.fn(), startSeparation: vi.fn(), cancelSeparation: vi.fn(), deleteStems: vi.fn() }))
vi.mock('../../api/midi', () => ({ getMidiStatus: vi.fn(), startTranscription: vi.fn(), cancelTranscription: vi.fn(), deleteMidi: vi.fn() }))
vi.mock('../../api/voices', async (original) => ({
  ...await original<typeof import('../../api/voices')>(),
  listVoices: vi.fn(), getActiveVoiceId: vi.fn(), setActiveVoiceId: vi.fn(), buildVoice: vi.fn(),
  getVoicePreparation: vi.fn(), voiceSeparationOptions: vi.fn(), listVoiceTrialSources: vi.fn(), listVoiceComparisons: vi.fn(),
}))
vi.mock('./WaveformPlayer.vue', () => ({ default: { render: () => null } }))

const idleStems: StemsStatus = { status: 'idle', error: null, stems: null }
const runningStems: StemsStatus = { ...idleStems, status: 'running' }
function midiStatus(state: MidiStatus['sources']['full']['status']): MidiStatus {
  return { available: ['full'], urls: {}, sources: {
    full: { status: state, error: null }, vocals: { status: 'idle', error: null },
    drums: { status: 'idle', error: null }, bass: { status: 'idle', error: null }, other: { status: 'idle', error: null },
  } }
}
function voice(status: VoiceProfile['status']): VoiceProfile {
  return { id: '1234567890abcdef1234567890abcdef', name: 'My voice', created_at: '2026-10-01T10:00:00Z',
    recordings: [{ filename: 'song.wav', bytes: 100 }], status, stage: '', detail: '', progress_current: 0,
    progress_total: 1, error: '', error_code: '', trained_steps: 0, built_from: [], has_preview: false, usable: status === 'ready' }
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>((release) => { resolve = release })
  return { promise, resolve }
}
async function settle() {
  await nextTick()
  await nextTick()
  await nextTick()
  await nextTick()
}

let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  setLocale('en')
  vi.mocked(stemsApi.getSeparationStatus).mockResolvedValue(idleStems)
  vi.mocked(midiApi.getMidiStatus).mockResolvedValue(midiStatus('idle'))
  vi.mocked(voicesApi.listVoices).mockResolvedValue([])
  vi.mocked(voicesApi.getActiveVoiceId).mockReturnValue(null)
  vi.mocked(voicesApi.getVoicePreparation).mockResolvedValue({ status: 'idle' })
  vi.mocked(voicesApi.voiceSeparationOptions).mockResolvedValue([{ id: 'fast', available: true }])
  vi.mocked(voicesApi.listVoiceTrialSources).mockResolvedValue([])
  vi.mocked(voicesApi.listVoiceComparisons).mockResolvedValue([])
})
afterEach(() => {
  app?.unmount()
  app = undefined
  document.body.replaceChildren()
  vi.clearAllTimers()
  vi.useRealTimers()
})
function unmount() { app?.unmount(); app = undefined }
async function mount(component: Component, trackId = 1) {
  const track = ref(trackId)
  app = createApp({ render: () => h(component, { trackId: track.value, title: 'Song', lyrics: '', model: 'ace_step' }) })
  app.use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] }))
  const container = document.createElement('div')
  document.body.append(container)
  app.mount(container)
  await settle()
  return { container, track }
}
async function click(container: HTMLElement, label: string) {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === label || item.getAttribute('aria-label') === label)
  if (!button) throw new Error(`Missing button: ${label}`)
  button.click()
  await settle()
}
async function expand(container: HTMLElement) {
  const button = container.querySelector('button')
  if (!button) throw new Error('Missing panel header')
  button.click()
  await settle()
}

it('does not restart stems polling after the initial status request resolves past unmount', async () => {
  const request = deferred<StemsStatus>()
  vi.mocked(stemsApi.getSeparationStatus).mockReturnValue(request.promise)
  await mount(StemsPanel)
  unmount()
  request.resolve(runningStems)
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(stemsApi.getSeparationStatus).toHaveBeenCalledTimes(1)
})

it('does not restart MIDI polling after the initial status request resolves past unmount', async () => {
  const request = deferred<MidiStatus>()
  vi.mocked(midiApi.getMidiStatus).mockReturnValue(request.promise)
  await mount(MidiPanel)
  unmount()
  request.resolve(midiStatus('running'))
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(midiApi.getMidiStatus).toHaveBeenCalledTimes(1)
})

it('invalidates an in-flight stems poll when its track changes', async () => {
  const request = deferred<StemsStatus>()
  vi.mocked(stemsApi.getSeparationStatus).mockResolvedValueOnce(runningStems).mockReturnValueOnce(request.promise).mockResolvedValue(idleStems)
  const { container, track } = await mount(StemsPanel)
  await expand(container)
  await vi.advanceTimersByTimeAsync(2000)
  track.value = 2
  await settle()
  const oldSignal = vi.mocked(stemsApi.getSeparationStatus).mock.calls[1]?.[1]
  expect(oldSignal?.aborted).toBe(true)
  request.resolve({ ...idleStems, status: 'failed', error: 'Previous track error' })
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(stemsApi.getSeparationStatus).toHaveBeenCalledTimes(3)
  expect(container.textContent).not.toContain('Previous track error')
})

it('invalidates an in-flight MIDI poll during unmount', async () => {
  const request = deferred<MidiStatus>()
  vi.mocked(midiApi.getMidiStatus).mockResolvedValueOnce(midiStatus('running')).mockReturnValue(request.promise)
  await mount(MidiPanel)
  await vi.advanceTimersByTimeAsync(2000)
  unmount()
  const signal = vi.mocked(midiApi.getMidiStatus).mock.calls[1]?.[1]
  expect(signal?.aborted).toBe(true)
  request.resolve(midiStatus('running'))
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(midiApi.getMidiStatus).toHaveBeenCalledTimes(2)
})

it('ignores a stems start response after unmount', async () => {
  const request = deferred<StemsStatus>()
  vi.mocked(stemsApi.startSeparation).mockReturnValue(request.promise)
  const { container } = await mount(StemsPanel)
  await expand(container)
  await click(container, 'Split into stems')
  unmount()
  request.resolve(runningStems)
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(stemsApi.getSeparationStatus).toHaveBeenCalledTimes(1)
})

it('ignores a MIDI start response after unmount', async () => {
  const request = deferred<MidiStatus>()
  vi.mocked(midiApi.startTranscription).mockReturnValue(request.promise)
  const { container } = await mount(MidiPanel)
  await expand(container)
  await click(container, 'Recognize')
  unmount()
  request.resolve(midiStatus('running'))
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(midiApi.getMidiStatus).toHaveBeenCalledTimes(1)
})

it('ignores a stems cancellation response belonging to the previous track', async () => {
  const request = deferred<StemsStatus>()
  vi.mocked(stemsApi.getSeparationStatus).mockResolvedValueOnce(runningStems).mockResolvedValue(idleStems)
  vi.mocked(stemsApi.cancelSeparation).mockReturnValue(request.promise)
  const { container, track } = await mount(StemsPanel)
  await expand(container)
  await click(container, 'Cancel')
  track.value = 2
  await settle()
  request.resolve({ ...idleStems, status: 'failed', error: 'Previous track error' })
  await settle()
  expect(stemsApi.getSeparationStatus).toHaveBeenCalledWith(2, expect.anything())
  expect(container.textContent).not.toContain('Previous track error')
})

it('ignores a MIDI cancellation response belonging to the previous track', async () => {
  const request = deferred<MidiStatus>()
  vi.mocked(midiApi.getMidiStatus).mockResolvedValueOnce(midiStatus('running')).mockResolvedValue(midiStatus('idle'))
  vi.mocked(midiApi.cancelTranscription).mockReturnValue(request.promise)
  const { container, track } = await mount(MidiPanel)
  await expand(container)
  await click(container, 'Cancel')
  track.value = 2
  await settle()
  const oldStatus = midiStatus('failed')
  oldStatus.sources.full.error = 'Previous track error'
  request.resolve(oldStatus)
  await settle()
  expect(midiApi.getMidiStatus).toHaveBeenCalledWith(2, expect.anything())
  expect(container.textContent).not.toContain('Previous track error')
})

it('keeps global voice selection intact when a selector request finishes after unmount', async () => {
  const request = deferred<VoiceProfile[]>()
  vi.mocked(voicesApi.listVoices).mockReturnValue(request.promise)
  vi.mocked(voicesApi.getActiveVoiceId).mockReturnValue(voice('ready').id)
  await mount(VoiceSelect)
  unmount()
  request.resolve([])
  await settle()
  expect(voicesApi.setActiveVoiceId).not.toHaveBeenCalled()
})

it('does not start voice-build polling after a late initial list response', async () => {
  const request = deferred<VoiceProfile[]>()
  vi.mocked(voicesApi.listVoices).mockReturnValue(request.promise)
  await mount(VoiceClonePage)
  unmount()
  request.resolve([voice('training')])
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(voicesApi.listVoices).toHaveBeenCalledTimes(1)
})

it('does not start voice-build polling after a build response arrives past unmount', async () => {
  const request = deferred<VoiceProfile>()
  vi.mocked(voicesApi.listVoices).mockResolvedValue([voice('idle')])
  vi.mocked(voicesApi.getVoicePreparation).mockResolvedValue(preparedVoice())
  vi.mocked(voicesApi.buildVoice).mockReturnValue(request.promise)
  const { container } = await mount(VoiceClonePage)
  await click(container, 'Build')
  await click(container, 'Build voice')
  expect(voicesApi.buildVoice).toHaveBeenCalledTimes(1)
  unmount()
  request.resolve(voice('queued'))
  await settle()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(voicesApi.listVoices).toHaveBeenCalledTimes(1)
})

it('does not overlap voice selector or voice-build list requests', async () => {
  const request = deferred<VoiceProfile[]>()
  vi.mocked(voicesApi.listVoices).mockReturnValue(request.promise)
  await mount(VoiceSelect)
  await vi.advanceTimersByTimeAsync(12_000)
  expect(voicesApi.listVoices).toHaveBeenCalledTimes(1)
  unmount()
  vi.mocked(voicesApi.listVoices).mockClear().mockResolvedValueOnce([voice('training')]).mockReturnValue(request.promise)
  await mount(VoiceClonePage)
  await vi.advanceTimersByTimeAsync(12_000)
  expect(voicesApi.listVoices).toHaveBeenCalledTimes(2)
})
