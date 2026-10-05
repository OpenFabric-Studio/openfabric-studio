// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import VoiceProfilesPanel from './VoiceProfilesPanel.vue'
import * as api from '../../api/voiceProfiles'
import type { SpeechCloneEngineStatus, SpeechCloneTrialResponse, SpeechVoiceProfile } from '../../api/voiceProfiles'
import type { StarterSpeechVoice } from '../../api/contracts'
import { ApiError } from '../../api/http'
import en from '../../locales/en'
import { speechWorkspaceEn } from '../../locales/speechWorkspace'
import { hasOpenDialog } from '../../composables/useDialogA11y'

vi.mock('../../api/localEngines', async original => ({ ...await original<typeof import('../../api/localEngines')>(),
  listLocalEngines: vi.fn().mockResolvedValue({ video_engine: 'ltx', video_preference: 'ltx', note: 'Song videos stay on LTX.', engines: [] }),
}))
vi.mock('../../api/voiceProfiles', async original => ({ ...await original<typeof import('../../api/voiceProfiles')>(),
  listSpeechVoiceProfiles: vi.fn(), getSpeechCloneEngine: vi.fn(),
  createSpeechVoiceProfile: vi.fn(), deleteSpeechVoiceProfile: vi.fn(), startSpeechCloneTrial: vi.fn(),
  listStarterSpeechVoices: vi.fn(), importStarterSpeechVoice: vi.fn(),
}))

const starter: StarterSpeechVoice = { id: 'vctk-p225', name: 'VCTK p225', accent: 'English · Southern England', transcript: 'Please call Stella.', duration_seconds: 4.2, sample_rate_hz: 48000, audio_url: '/api/voice-profiles/starter-voices/vctk-p225/audio', source_url: 'https://datashare.ed.ac.uk/handle/10283/3443', license_name: 'CC BY 4.0', license_url: 'https://creativecommons.org/licenses/by/4.0/', attribution: 'VCTK Corpus 0.92 · University of Edinburgh' }
const imported = { ...profile('33333333333333333333333333333333', starter.name), starter_voice_id: starter.id, notes: starter.transcript }

function profile(id = '11111111111111111111111111111111', name = 'First narrator'): SpeechVoiceProfile {
  return { id, name, consent_confirmed: true, reference_audio_path: '/private/library/reference.wav', notes: 'Reference transcript', created_at: '2026-10-02', updated_at: '2026-10-02', engine_hints: null }
}
const first = profile()
const second = profile('22222222222222222222222222222222', 'Second narrator')
function trial(profileId = first.id): SpeechCloneTrialResponse {
  return { profile_id: profileId, engine: 'gpt-sovits', status: 'completed', detail: 'Internal detail at /private/engine', output_path: '/private/library/trials/trial.wav', trial_id: 'trial-1', install_hints: [] }
}
let app: App | undefined
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.listSpeechVoiceProfiles).mockResolvedValue([first, second])
  vi.mocked(api.getSpeechCloneEngine).mockResolvedValue({ installed: true, mock: false, api_reachable: true, install_hints: ['Install the official engine.'], root: '/private/engine', api_base_url: 'http://localhost:9880' })
  vi.mocked(api.deleteSpeechVoiceProfile).mockResolvedValue(undefined)
  vi.mocked(api.listStarterSpeechVoices).mockResolvedValue([starter])
  vi.mocked(api.importStarterSpeechVoice).mockResolvedValue(imported)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount(options: { activity?: (message: string) => void; shown?: { value: boolean }; active?: { value: boolean } } = {}) {
  app = createApp({ render: () => h('div', { style: { display: options.shown?.value === false ? 'none' : '' } }, [h(VoiceProfilesPanel, { onActivity: options.activity, active: options.active?.value ?? true })]) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { ...en, speechWorkspace: speechWorkspaceEn } } }))
  const container = document.body.appendChild(document.createElement('div'))
  app.mount(container)
  await settle()
  return container
}
function button(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label || item.getAttribute('aria-label') === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
async function click(container: HTMLElement, label: string) { button(container, label).click(); await settle() }
function field(container: HTMLElement, label: string): HTMLInputElement | HTMLTextAreaElement {
  const found = container.querySelector(`[aria-label="${label}"]`)
  if (!(found instanceof HTMLInputElement) && !(found instanceof HTMLTextAreaElement)) throw new Error(`Missing field: ${label}`)
  return found
}
async function change(container: HTMLElement, label: string, value: string) {
  const found = field(container, label); found.value = value
  found.dispatchEvent(new Event('input', { bubbles: true })); await settle()
}
function form(container: HTMLElement, label: string): HTMLFormElement {
  const found = container.querySelector(`form[aria-label="${label}"]`)
  if (!(found instanceof HTMLFormElement)) throw new Error(`Missing form: ${label}`)
  return found
}
async function submit(container: HTMLElement, label: string) { form(container, label).dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle() }
async function creationDraft(container: HTMLElement) {
  await click(container, 'New profile')
  await change(container, 'Profile name', 'New narrator')
  const audio = field(container, 'Reference audio (wav or flac)')
  Object.defineProperty(audio, 'files', { configurable: true, value: [new File(['audio fixture'], 'clip.wav', { type: 'audio/wav' })] })
  audio.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>(release => { resolve = release })
  return { promise, resolve }
}

it('shows licensed starters without depending on profile or engine loading', async () => {
  vi.mocked(api.listSpeechVoiceProfiles).mockRejectedValue(new Error('/private/library'))
  vi.mocked(api.getSpeechCloneEngine).mockRejectedValue(new Error('/private/engine'))
  const container = await mount()
  expect(container.querySelector('[aria-label="Starter voices"] h3')?.textContent).toBe(starter.accent)
  expect(container.querySelector('audio')?.getAttribute('src')).toBe(starter.audio_url)
  expect(container.textContent).toContain('Licensed reference')
  expect(container.textContent).not.toContain('/private/')
})
it('allows importing after the library resolves while the independently owned engine check is pending', async () => {
  const engine = deferred<SpeechCloneEngineStatus>()
  vi.mocked(api.getSpeechCloneEngine).mockReturnValue(engine.promise)
  vi.mocked(api.listSpeechVoiceProfiles).mockResolvedValue([])
  const container = await mount()
  expect(button(container, 'Add VCTK p225 to my voices').disabled).toBe(false)
  expect(button(container, 'New profile').disabled).toBe(false)
  expect(button(container, 'Refresh status').disabled).toBe(true)
  await click(container, 'Add VCTK p225 to my voices')
  expect(api.importStarterSpeechVoice).toHaveBeenCalledTimes(1)
  expect(form(container, 'Speech synthesis').textContent).toContain(starter.name)
  button(container, 'Refresh status').dispatchEvent(new Event('click', { bubbles: true })); await settle()
  expect(api.getSpeechCloneEngine).toHaveBeenCalledTimes(1)
  engine.resolve({ installed: false, mock: true, api_reachable: false }); await settle()
  expect(button(container, 'Refresh status').disabled).toBe(false)
  expect(form(container, 'Speech synthesis').textContent).toContain(starter.name)
})
it('imports a starter once and selects its licensed independent library copy', async () => {
  vi.mocked(api.listSpeechVoiceProfiles).mockResolvedValue([])
  const request = deferred<SpeechVoiceProfile>(); vi.mocked(api.importStarterSpeechVoice).mockReturnValue(request.promise)
  const container = await mount()
  const add = button(container, 'Add VCTK p225 to my voices')
  add.click(); add.dispatchEvent(new Event('click', { bubbles: true })); await settle()
  expect(api.importStarterSpeechVoice).toHaveBeenCalledTimes(1)
  expect(api.importStarterSpeechVoice).toHaveBeenCalledWith(starter.id, expect.any(AbortSignal))
  request.resolve(imported); await settle()
  expect(form(container, 'Speech synthesis').textContent).toContain(starter.name)
  expect(form(container, 'Speech synthesis').textContent).toContain('Licensed reference')
  expect(form(container, 'Speech synthesis').textContent).not.toContain(en.voiceProfiles.consentOk)
  expect(container.querySelector<HTMLElement>('#starter-voice-catalog')?.style.display).toBe('none')
  expect(document.activeElement).toBe(field(container, 'Text to speak'))
  await click(container, 'Browse starter voices')
  expect(button(container, 'Use VCTK p225')).toBeDefined()
})
it('collapses the catalog and focuses synthesis when an existing licensed voice is chosen', async () => {
  vi.mocked(api.listSpeechVoiceProfiles).mockResolvedValue([first, imported])
  const container = await mount(); await click(container, 'Browse starter voices'); await click(container, 'Use VCTK p225')
  expect(form(container, 'Speech synthesis').textContent).toContain(starter.name)
  expect(container.querySelector<HTMLElement>('#starter-voice-catalog')?.style.display).toBe('none')
  expect(document.activeElement).toBe(field(container, 'Text to speak'))
  expect(api.importStarterSpeechVoice).not.toHaveBeenCalled()
})
it('shows missing permission for a revoked starter profile instead of treating provenance as permission', async () => {
  vi.mocked(api.listSpeechVoiceProfiles).mockResolvedValue([{ ...imported, consent_confirmed: false }])
  const container = await mount()
  expect(container.querySelector('[aria-pressed=true]')?.textContent).toContain(en.voiceProfiles.consentMissing)
  expect(form(container, 'Speech synthesis').textContent).toContain(en.voiceProfiles.consentMissing)
  expect(form(container, 'Speech synthesis').textContent).not.toContain('Licensed reference')
  await change(container, 'Text to speak', 'Hello')
  expect(button(container, 'Generate speech trial').disabled).toBe(true)
})
it('rejects an import response belonging to another starter before adding it to the library', async () => {
  vi.mocked(api.importStarterSpeechVoice).mockResolvedValue({ ...imported, starter_voice_id: 'vctk-p237' })
  const container = await mount(); await click(container, 'Browse starter voices'); await click(container, 'Add VCTK p225 to my voices')
  expect(container.textContent).toContain('Could not add the starter voice')
  expect(form(container, 'Speech synthesis').textContent).toContain('First narrator')
  expect([...container.querySelectorAll('[aria-pressed]')].some(item => item.textContent?.includes(starter.name))).toBe(false)
})
it('preserves newer selection and speech text when an import finishes late', async () => {
  const request = deferred<SpeechVoiceProfile>(); vi.mocked(api.importStarterSpeechVoice).mockReturnValue(request.promise)
  const container = await mount(); await click(container, 'Browse starter voices')
  await change(container, 'Text to speak', 'Keep my text')
  await click(container, 'Add VCTK p225 to my voices'); await click(container, 'Second narrator')
  request.resolve(imported); await settle()
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
  expect(field(container, 'Text to speak').value).toBe('Keep my text')
  expect(button(container, starter.name)).toBeDefined()
  expect(container.querySelector<HTMLElement>('#starter-voice-catalog')?.style.display).not.toBe('none')
})
it('preserves a new creation draft opened while a starter import is pending', async () => {
  const request = deferred<SpeechVoiceProfile>(); vi.mocked(api.importStarterSpeechVoice).mockReturnValue(request.promise)
  const container = await mount(); await click(container, 'Browse starter voices'); await click(container, 'Add VCTK p225 to my voices')
  await creationDraft(container)
  request.resolve(imported); await settle()
  expect(form(container, 'Create speech profile').style.display).not.toBe('none')
  expect(field(container, 'Profile name').value).toBe('New narrator')
  expect(container.querySelector<HTMLElement>('#starter-voice-catalog')?.style.display).not.toBe('none')
})
it('keeps import errors private and retries deliberately without refreshing over the import', async () => {
  vi.mocked(api.importStarterSpeechVoice).mockRejectedValueOnce(new Error('token=secret /private/database')).mockResolvedValue(imported)
  const container = await mount(); await click(container, 'Browse starter voices'); await click(container, 'Add VCTK p225 to my voices')
  expect(container.textContent).toContain('Could not add the starter voice')
  expect(container.textContent).not.toContain('token=secret')
  expect(container.querySelector<HTMLElement>('#starter-voice-catalog')?.style.display).not.toBe('none')
  await click(container, 'Add VCTK p225 to my voices')
  expect(api.importStarterSpeechVoice).toHaveBeenCalledTimes(2)
  expect(api.listSpeechVoiceProfiles).toHaveBeenCalledTimes(1)
})
it('aborts a pending import and ignores its late completion after unmount', async () => {
  const request = deferred<SpeechVoiceProfile>(); vi.mocked(api.importStarterSpeechVoice).mockReturnValue(request.promise)
  const activity = vi.fn<(message: string) => void>()
  const container = await mount({ activity }); await click(container, 'Browse starter voices'); await click(container, 'Add VCTK p225 to my voices')
  const signal = vi.mocked(api.importStarterSpeechVoice).mock.calls[0]?.[1]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  const emitted = activity.mock.calls.length
  request.resolve(imported); await settle()
  expect(activity).toHaveBeenCalledTimes(emitted)
})
it('does not reclaim focus when a starter import succeeds after Speech is hidden', async () => {
  const request = deferred<SpeechVoiceProfile>(); vi.mocked(api.importStarterSpeechVoice).mockReturnValue(request.promise)
  const active = ref(true)
  const container = await mount({ active }); await click(container, 'Browse starter voices'); await click(container, 'Add VCTK p225 to my voices')
  const outside = document.body.appendChild(document.createElement('button')); outside.focus()
  active.value = false; await settle(); request.resolve(imported); await settle()
  expect(document.activeElement).toBe(outside)
  expect(form(container, 'Speech synthesis').textContent).toContain(starter.name)
})
it('does not move focus back to synthesis after a newer creation action follows Use voice', async () => {
  vi.mocked(api.listSpeechVoiceProfiles).mockResolvedValue([first, imported])
  const container = await mount(); await click(container, 'Browse starter voices')
  button(container, 'Use VCTK p225').click(); button(container, 'New profile').click(); await settle()
  expect(form(container, 'Create speech profile').style.display).not.toBe('none')
  expect(document.activeElement).toBe(field(container, 'Profile name'))
})
it.each(['completed', 'mock_completed'] as const)('plays and downloads identifier-routed $status trial audio while keeping raw paths private', async status => {
  const id = 'a'.repeat(32)
  vi.mocked(api.startSpeechCloneTrial).mockResolvedValue({ ...trial(), trial_id: id, status })
  const active = ref(true)
  const container = await mount({ active }); await change(container, 'Text to speak', 'Hello'); await submit(container, 'Speech synthesis')
  const audio = container.querySelector(`[data-speech-trial] audio`)
  if (!(audio instanceof HTMLAudioElement)) throw new Error('Missing generated audio')
  expect(audio.getAttribute('src')).toBe(`/api/speech-clone/trials/${id}/audio`)
  expect(container.querySelector('[data-speech-trial] a[download]')?.getAttribute('href')).toBe(`/api/speech-clone/trials/${id}/audio`)
  expect(container.textContent).not.toContain('/private/')
  if (status === 'mock_completed') expect(container.querySelector('[data-speech-trial]')?.textContent).toContain('silent placeholder')
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined)
  active.value = false; await settle()
  expect(pause).toHaveBeenCalled()
})

it('opens creation on demand and requires explicit consent before submitting', async () => {
  const container = await mount()
  expect([...container.querySelectorAll('input[type=file]')].filter(input => !input.closest('[data-local-engines]'))).toEqual([])
  await creationDraft(container)
  expect(button(container, 'Create profile').disabled).toBe(true)
  await submit(container, 'Create speech profile')
  expect(api.createSpeechVoiceProfile).not.toHaveBeenCalled()
  expect(container.textContent).toContain('Consent is required')
})

it('preserves the creation draft while the mounted workspace is hidden', async () => {
  const shown = ref(true)
  const container = await mount({ shown })
  await creationDraft(container)
  shown.value = false; await settle(); shown.value = true; await settle()
  expect(field(container, 'Profile name').value).toBe('New narrator')
  expect(api.listSpeechVoiceProfiles).toHaveBeenCalledTimes(1)
})

it('searches the profile library and selects the synthesis profile without a second picker', async () => {
  const container = await mount()
  await change(container, 'Search speech profiles', 'second')
  expect(container.querySelector('[aria-pressed=true]')).toBeNull()
  await click(container, 'Second narrator')
  expect(container.querySelector('[aria-pressed=true]')?.textContent).toContain('Second narrator')
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
  expect([...container.querySelectorAll('select')].filter(input => !input.closest('[data-local-engines]')).map(input => input.getAttribute('aria-label'))).toEqual(['Narration language'])
  await change(container, 'Search speech profiles', '')
  expect(button(container, 'First narrator')).toBeDefined()
})

it('shows installed, mock and API state independently without exposing engine paths', async () => {
  vi.mocked(api.getSpeechCloneEngine).mockResolvedValue({ installed: false, mock: true, api_reachable: true, root: '/private/engine', install_hints: ['Install the official engine.'] })
  const container = await mount()
  const status = container.querySelector('[aria-label="Speech engine status"]')
  expect(status?.textContent).toContain('Engine checkoutNot detected')
  expect(status?.textContent).toContain('Mock modeOn')
  expect(status?.textContent).toContain('Speech APIConnected')
  expect(container.textContent).not.toContain('synthesis invoke coming next')
  expect(container.textContent).not.toContain('/private/engine')
  expect(status?.querySelector('details')?.hasAttribute('open')).toBe(false)
})

it('requires a named deletion confirmation and restores keyboard focus on Escape', async () => {
  const container = await mount()
  const opener = button(container, 'Delete profile'); opener.focus()
  await click(container, 'Delete profile')
  expect(api.deleteSpeechVoiceProfile).not.toHaveBeenCalled()
  const dialog = container.querySelector('[role=dialog][aria-modal=true]')
  expect(dialog?.textContent).toContain('Delete First narrator?')
  const labelledBy = dialog?.getAttribute('aria-labelledby')
  expect(labelledBy ? document.getElementById(labelledBy)?.textContent : null).toBe('Delete First narrator?')
  expect(document.activeElement).toBe(dialog?.querySelector('button'))
  document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await settle()
  expect(container.querySelector('[role=dialog]')).toBeNull()
  expect(document.activeElement).toBe(opener)
})

it('deletes once and preserves a newer profile selection while deletion is pending', async () => {
  const request = deferred<void>()
  vi.mocked(api.deleteSpeechVoiceProfile).mockReturnValue(request.promise)
  const container = await mount()
  await click(container, 'Delete profile')
  const confirm = button(container, 'Delete')
  confirm.click(); confirm.dispatchEvent(new Event('click', { bubbles: true })); await settle()
  expect(api.deleteSpeechVoiceProfile).toHaveBeenCalledTimes(1)
  await click(container, 'Second narrator')
  request.resolve(undefined); await settle()
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
  expect([...container.querySelectorAll('button')].some(item => item.textContent?.trim() === 'First narrator')).toBe(false)
})

it('creates once and keeps a newer selection when profile creation completes', async () => {
  const request = deferred<SpeechVoiceProfile>()
  vi.mocked(api.createSpeechVoiceProfile).mockReturnValue(request.promise)
  const container = await mount()
  await creationDraft(container)
  field(container, en.voiceProfiles.consentLabel).click(); await settle()
  await submit(container, 'Create speech profile'); await submit(container, 'Create speech profile')
  expect(api.createSpeechVoiceProfile).toHaveBeenCalledTimes(1)
  await click(container, 'Second narrator')
  request.resolve(profile('33333333333333333333333333333333', 'New narrator')); await settle()
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
  expect(button(container, 'New narrator')).toBeDefined()
})

it('prevents duplicate trials and emits translated pending activity', async () => {
  const request = deferred<SpeechCloneTrialResponse>()
  vi.mocked(api.startSpeechCloneTrial).mockReturnValue(request.promise)
  const activity = vi.fn<(message: string) => void>()
  const container = await mount({ activity })
  await change(container, 'Text to speak', 'Hello')
  await submit(container, 'Speech synthesis'); await submit(container, 'Speech synthesis')
  expect(api.startSpeechCloneTrial).toHaveBeenCalledTimes(1)
  expect(button(container, 'Generating speech trial…').disabled).toBe(true)
  expect(activity).toHaveBeenLastCalledWith('Generating speech trial…')
  request.resolve(trial()); await settle()
  expect(activity).toHaveBeenLastCalledWith('')
  expect(container.textContent).toContain('Speech trial synthesized successfully.')
  expect(container.textContent).toContain('Playback is not available')
  expect(container.textContent).not.toContain('/private/')
  expect(container.querySelector('audio')).toBeNull()
})

it('aborts a previous trial and ignores its completion after profile selection changes', async () => {
  const request = deferred<SpeechCloneTrialResponse>()
  vi.mocked(api.startSpeechCloneTrial).mockReturnValue(request.promise)
  const container = await mount()
  await change(container, 'Text to speak', 'Hello'); await submit(container, 'Speech synthesis')
  const signal = vi.mocked(api.startSpeechCloneTrial).mock.calls[0]?.[2]
  await click(container, 'Second narrator')
  expect(signal?.aborted).toBe(true)
  request.resolve(trial()); await settle()
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
  expect(container.textContent).not.toContain('Speech trial synthesized successfully.')
})

it('aborts owned requests and ignores late completion after unmount', async () => {
  const request = deferred<SpeechCloneTrialResponse>()
  vi.mocked(api.startSpeechCloneTrial).mockReturnValue(request.promise)
  const activity = vi.fn<(message: string) => void>()
  const container = await mount({ activity })
  await change(container, 'Text to speak', 'Hello'); await submit(container, 'Speech synthesis')
  const signal = vi.mocked(api.startSpeechCloneTrial).mock.calls[0]?.[2]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  const emitted = activity.mock.calls.length
  request.resolve(trial()); await settle()
  expect(activity).toHaveBeenCalledTimes(emitted)
  expect(api.listSpeechVoiceProfiles).toHaveBeenCalledTimes(1)
})

it('keeps raw API failures private and emits a readable error state', async () => {
  vi.mocked(api.startSpeechCloneTrial).mockRejectedValue(new ApiError('Permission denied: /private/config/secret', 500))
  const activity = vi.fn<(message: string) => void>()
  const container = await mount({ activity })
  await change(container, 'Text to speak', 'Hello'); await submit(container, 'Speech synthesis')
  expect(container.textContent).toContain('Speech trial failed.')
  expect(container.textContent).not.toContain('/private/config/secret')
  expect(activity).toHaveBeenLastCalledWith('Speech trial failed.')
})

it('does not allow synthesis with a profile whose consent is missing', async () => {
  vi.mocked(api.listSpeechVoiceProfiles).mockResolvedValue([{ ...first, consent_confirmed: false }])
  const container = await mount()
  await change(container, 'Text to speak', 'Hello')
  expect(button(container, 'Generate speech trial').disabled).toBe(true)
  await submit(container, 'Speech synthesis')
  expect(api.startSpeechCloneTrial).not.toHaveBeenCalled()
  expect(container.textContent).toContain('Consent missing')
})

it('waits for the initial library before enabling creation', async () => {
  const request = deferred<SpeechVoiceProfile[]>()
  vi.mocked(api.listSpeechVoiceProfiles).mockReturnValue(request.promise)
  const container = await mount()
  expect(button(container, 'New profile').disabled).toBe(true)
  request.resolve([first]); await settle()
  expect(button(container, 'New profile').disabled).toBe(false)
})

it('does not refresh a stale library over a pending profile creation', async () => {
  const request = deferred<SpeechVoiceProfile>()
  vi.mocked(api.createSpeechVoiceProfile).mockReturnValue(request.promise)
  const container = await mount()
  await creationDraft(container)
  field(container, en.voiceProfiles.consentLabel).click(); await settle()
  await submit(container, 'Create speech profile')
  expect(button(container, 'Refresh status').disabled).toBe(true)
  button(container, 'Refresh status').dispatchEvent(new Event('click', { bubbles: true })); await settle()
  expect(api.listSpeechVoiceProfiles).toHaveBeenCalledTimes(1)
  request.resolve(profile('33333333333333333333333333333333', 'New narrator')); await settle()
  expect(button(container, 'Refresh status').disabled).toBe(false)
})

it('releases a pending deletion dialog on mode deactivation and retains request ownership', async () => {
  const active = ref(true)
  const shown = ref(true)
  const request = deferred<void>()
  vi.mocked(api.deleteSpeechVoiceProfile).mockReturnValue(request.promise)
  const activity = vi.fn<(message: string) => void>()
  const container = await mount({ active, shown, activity })
  await click(container, 'Delete profile'); await click(container, 'Delete')
  const outside = document.body.appendChild(document.createElement('button')); outside.focus()
  active.value = false; shown.value = false; await settle()
  expect(container.querySelector('[role=dialog]')).toBeNull()
  expect(hasOpenDialog()).toBe(false)
  expect(document.activeElement).toBe(outside)
  expect(vi.mocked(api.deleteSpeechVoiceProfile).mock.calls[0]?.[1]?.aborted).toBe(false)
  expect(activity).toHaveBeenLastCalledWith('Deleting speech profile…')
  request.resolve(undefined); await settle()
  active.value = true; shown.value = true; await settle()
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
  expect(container.querySelector('[role=dialog]')).toBeNull()
  expect(activity).toHaveBeenLastCalledWith('')
})

it('does not reclaim focus for a creation form after its mode is hidden', async () => {
  const active = ref(true)
  const shown = ref(true)
  const container = await mount({ active, shown })
  button(container, 'New profile').click()
  const outside = document.body.appendChild(document.createElement('button')); outside.focus()
  active.value = false; shown.value = false; await settle()
  expect(document.activeElement).toBe(outside)
  active.value = true; shown.value = true; await settle()
  expect(field(container, 'Profile name').value).toBe('')
})

it('keeps a failed creation draft and private error details out of the UI', async () => {
  vi.mocked(api.createSpeechVoiceProfile).mockRejectedValue(new ApiError('Bad database /private/library', 500))
  const container = await mount()
  await creationDraft(container)
  field(container, en.voiceProfiles.consentLabel).click(); await settle()
  await submit(container, 'Create speech profile')
  expect(container.textContent).toContain('Could not create the speech profile.')
  expect(container.textContent).not.toContain('/private/library')
  expect(field(container, 'Profile name').value).toBe('New narrator')
  expect(button(container, 'Create profile').disabled).toBe(false)
})

it('keeps failed deletion reviewable and allows a deliberate retry', async () => {
  vi.mocked(api.deleteSpeechVoiceProfile).mockRejectedValueOnce(new ApiError('Bad database /private/library', 500)).mockResolvedValue(undefined)
  const container = await mount()
  await click(container, 'Delete profile'); await click(container, 'Delete')
  expect(container.querySelector('[role=dialog]')?.textContent).toContain('Could not delete the speech profile.')
  expect(container.textContent).not.toContain('/private/library')
  expect(button(container, 'Delete').disabled).toBe(false)
  await click(container, 'Delete')
  expect(container.querySelector('[role=dialog]')).toBeNull()
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
})

it('still reports engine state when profile loading fails', async () => {
  vi.mocked(api.listSpeechVoiceProfiles).mockRejectedValue(new ApiError('Bad database /private/library', 500))
  const container = await mount()
  expect(container.textContent).toContain('Could not load speech profiles.')
  expect(container.querySelector('[aria-label="Speech engine status"]')?.textContent).toContain('Speech APIConnected')
  expect(container.textContent).not.toContain('/private/library')
})

it('shows unknown engine state when its check fails without blocking profile selection', async () => {
  vi.mocked(api.getSpeechCloneEngine).mockRejectedValue(new ApiError('Secret engine configuration', 500))
  const container = await mount()
  expect(container.querySelector('[aria-label="Speech engine status"]')?.textContent).toContain('Speech APIUnknown')
  await click(container, 'Second narrator')
  expect(form(container, 'Speech synthesis').textContent).toContain('Second narrator')
  expect(container.textContent).not.toContain('Secret engine configuration')
})

it('aborts a pending creation on teardown without clearing another session or refreshing', async () => {
  const request = deferred<SpeechVoiceProfile>()
  vi.mocked(api.createSpeechVoiceProfile).mockReturnValue(request.promise)
  const activity = vi.fn<(message: string) => void>()
  const container = await mount({ activity })
  await creationDraft(container)
  field(container, en.voiceProfiles.consentLabel).click(); await settle()
  await submit(container, 'Create speech profile')
  const signal = vi.mocked(api.createSpeechVoiceProfile).mock.calls[0]?.[1]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  const emitted = activity.mock.calls.length
  request.resolve(profile('33333333333333333333333333333333', 'New narrator')); await settle()
  expect(activity).toHaveBeenCalledTimes(emitted)
  expect(api.listSpeechVoiceProfiles).toHaveBeenCalledTimes(1)
})

it('aborts initial loading on teardown and ignores both late responses', async () => {
  const profiles = deferred<SpeechVoiceProfile[]>()
  vi.mocked(api.listSpeechVoiceProfiles).mockReturnValue(profiles.promise)
  const activity = vi.fn<(message: string) => void>()
  await mount({ activity })
  const signal = vi.mocked(api.listSpeechVoiceProfiles).mock.calls[0]?.[0]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  const emitted = activity.mock.calls.length
  profiles.resolve([first]); await settle()
  expect(activity).toHaveBeenCalledTimes(emitted)
  expect(api.getSpeechCloneEngine).toHaveBeenCalledTimes(1)
})

const outcomes: { status: SpeechCloneTrialResponse['status']; message: string }[] = [
  { status: 'mock_completed', message: speechWorkspaceEn.trialMock },
  { status: 'engine_ready', message: speechWorkspaceEn.trialEngineReady },
  { status: 'api_unavailable', message: speechWorkspaceEn.trialApiUnavailable },
  { status: 'engine_not_installed', message: speechWorkspaceEn.trialMissing },
  { status: 'failed', message: en.voiceProfiles.err.trial },
]
it.each(outcomes)('reports $status as a translated outcome without raw details', async ({ status, message }) => {
  vi.mocked(api.startSpeechCloneTrial).mockResolvedValue({ ...trial(), status, detail: 'Internal failure /private/library' })
  const container = await mount()
  await change(container, 'Text to speak', 'Hello'); await submit(container, 'Speech synthesis')
  expect(container.textContent).toContain(message)
  expect(container.textContent).not.toContain('/private/library')
  expect(button(container, 'Generate speech trial').disabled).toBe(false)
})
