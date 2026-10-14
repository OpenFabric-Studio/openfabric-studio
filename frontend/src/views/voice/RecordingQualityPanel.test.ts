// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import RecordingQualityPanel from './RecordingQualityPanel.vue'
import { recordingQCEn } from '../../locales/recordingQC'
import type { RecordingLabels, RecordingSession, VoiceProfileResponse } from '../../api/contracts'
import { voiceProfile } from './voiceTestFixtures'

const voice = voiceProfile('a'.repeat(32))
const session: RecordingSession = { id: 'b'.repeat(32), voice_id: voice.id, purpose: 'training', labels: { language: 'Original Spanish', register: 'chest', articulation: 'clear', expression: 'gentle' }, original_filename: 'song.wav', relative_path: 'recordings/song.wav', original_sha256: 'c'.repeat(64), file_bytes: 100, created_at: 'now', format: 'WAV', sample_rate: 44100, channels: 2, subtype: 'PCM_16', duration_sec: 10,
  qc: { peak: .999, clipped_fraction: .01, rms_dbfs: -20, near_silence_fraction: .1, noise_floor_proxy_dbfs: -60, samples_analyzed: 882000, warnings: ['full_scale_samples', 'noise_estimate_is_amplitude_proxy', 'capture_differs_from_guidance'] } }
let app: App | undefined
let sessions: RecordingSession[]
let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  sessions = [session]
  fetchMock = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === 'POST' ? session : { voice_id: voice.id, sessions })))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })
async function settle() { for (let index = 0; index < 12; index++) { await Promise.resolve(); await nextTick() } }
async function mount(selected = ref<VoiceProfileResponse>(voice), active = ref(true), labels: RecordingLabels[] = []) {
  app = createApp({ render: () => h(RecordingQualityPanel, { voice: selected.value, active: active.value, onLabels: (value: RecordingLabels) => labels.push(value) }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { recordingQC: recordingQCEn } } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle(); return container
}
function button(container: HTMLElement, text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!found) throw new Error(`Missing button: ${text}`)
  return found
}
async function input(container: HTMLElement, label: string, value: string) {
  const field = container.querySelector(`[aria-label="${label}"]`)
  if (!(field instanceof HTMLInputElement)) throw new Error(`Missing field: ${label}`)
  field.value = value; field.dispatchEvent(new Event('input', { bubbles: true })); await settle()
}
it('shows actual capture format and amplitude limits without relabeling it as guidance quality', async () => {
  const container = await mount()
  expect(container.textContent).toContain('44100 Hz')
  expect(container.textContent).toContain('PCM_16')
  expect(container.textContent).toContain(session.original_sha256)
  expect(container.textContent).toContain(recordingQCEn.warnings.noise_estimate_is_amplitude_proxy)
  expect(container.textContent).toContain(recordingQCEn.warnings.capture_differs_from_guidance)
  expect(button(container, recordingQCEn.inspect).disabled).toBe(true)
})
it('emits capture labels for future uploads while preserving first stored labels', async () => {
  const emitted: RecordingLabels[] = []
  const container = await mount(ref(voice), ref(true), emitted)
  await input(container, recordingQCEn.language, 'Future English')
  await input(container, recordingQCEn.register, 'head')
  expect(emitted.at(-1)).toMatchObject({ language: 'Future English', register: 'head' })
  expect(container.textContent).toContain('Original Spanish')
  expect(container.textContent).toContain(recordingQCEn.immutable)
  expect(fetchMock.mock.calls.some(call => call[1]?.method === 'POST')).toBe(false)
})
it('inspects an unmeasured training source without exposing a held-out purpose selector', async () => {
  sessions = []
  const container = await mount()
  button(container, recordingQCEn.inspect).click(); await settle()
  const request = fetchMock.mock.calls.find(call => call[1]?.method === 'POST')?.[1]
  expect(JSON.parse(String(request?.body))).toMatchObject({ purpose: 'training', filename: 'song.wav' })
  expect(container.querySelector('select option[value="held_out"]')).toBeNull()
})
it('displays held-out records separately without offering them for training inspection', async () => {
  sessions = [{ ...session, purpose: 'held_out', original_filename: 'held-out.wav', relative_path: 'trials/sources/held-out.wav' }]
  const container = await mount()
  expect(container.textContent).toContain(recordingQCEn.heldOut)
  expect(container.textContent).toContain('held-out.wav')
  expect([...container.querySelectorAll('select option')].some(option => option.textContent === 'held-out.wav')).toBe(false)
})
it('blocks inspection after a failed load and sanitizes the error', async () => {
  fetchMock.mockRejectedValueOnce(new Error('/private/original.wav'))
  const container = await mount()
  expect(button(container, recordingQCEn.inspect).disabled).toBe(true)
  expect(container.querySelector('[role=alert]')?.textContent).toContain(recordingQCEn.errors.unknown)
  expect(container.textContent).not.toContain('/private/')
})
it('ignores inspection results after switching voices and aborts the owned request', async () => {
  sessions = []
  let release: (value: Response) => void = () => { throw new Error('Not initialized') }
  const selected = ref<VoiceProfileResponse>(voice)
  const container = await mount(selected)
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
  button(container, recordingQCEn.inspect).click(); await settle()
  const signal: unknown = fetchMock.mock.calls.at(-1)?.[1]?.signal
  selected.value = { ...voice, id: 'd'.repeat(32), recordings: [] }
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ voice_id: selected.value.id, sessions: [] })))
  await settle()
  release(new Response(JSON.stringify(session))); await settle()
  expect(signal instanceof AbortSignal && signal.aborted).toBe(true)
  expect(container.textContent).not.toContain('Original Spanish')
})
