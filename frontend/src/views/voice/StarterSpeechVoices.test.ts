// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import StarterSpeechVoices from './StarterSpeechVoices.vue'
import * as api from '../../api/voiceProfiles'
import type { StarterSpeechVoice, SpeechVoiceProfile } from '../../api/contracts'
import { speechWorkspaceEn } from '../../locales/speechWorkspace'

vi.mock('../../api/voiceProfiles', async original => ({ ...await original<typeof import('../../api/voiceProfiles')>(), listStarterSpeechVoices: vi.fn() }))
const ids = ['vctk-p225', 'vctk-p237', 'vctk-p294', 'vctk-p326']
const voices: StarterSpeechVoice[] = ids.map(id => ({ id, name: `VCTK ${id.slice(5)}`, accent: 'English', transcript: `Exact source words for ${id}.`, duration_seconds: 4.2, sample_rate_hz: 48000, audio_url: `/api/voice-profiles/starter-voices/${id}/audio`, source_url: 'https://datashare.ed.ac.uk/handle/10283/3443', license_name: 'CC BY 4.0', license_url: 'https://creativecommons.org/licenses/by/4.0/', attribution: 'VCTK Corpus 0.92 · University of Edinburgh' }))
let app: App | undefined
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.listStarterSpeechVoices).mockResolvedValue(voices) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 10; i++) await nextTick() }
async function mount(initialProfiles: SpeechVoiceProfile[] = []) {
  const profiles = ref(initialProfiles), active = ref(true), expanded = ref<boolean | null>(null)
  const imports: string[] = [], selections: string[] = []
  app = createApp({ render: () => h(StarterSpeechVoices, { profiles: profiles.value, active: active.value, expanded: expanded.value, 'onUpdate:expanded': (value: boolean) => { expanded.value = value }, onImport: (id: string) => imports.push(id), onSelect: (id: string) => selections.push(id) }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { speechWorkspace: speechWorkspaceEn } } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  return { container, profiles, active, imports, selections }
}
function button(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label || item.getAttribute('aria-label') === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
it('renders the dynamic source catalog, exact transcripts, measured properties and attribution', async () => {
  const { container } = await mount()
  expect(container.querySelectorAll('[data-starter-voice]')).toHaveLength(4)
  expect(container.querySelectorAll('audio')).toHaveLength(4)
  expect(container.querySelectorAll('audio')[0]?.getAttribute('src')).toBe(voices[0]?.audio_url)
  expect(container.querySelector('[data-starter-voice] h3')?.textContent).toBe(voices[0]?.accent)
  expect(container.querySelector('[data-starter-voice] details summary')?.textContent).toBe('Reference details')
  expect(container.textContent).toContain('4.2 s · 48 kHz')
  expect(container.textContent).toContain(voices[0]?.transcript)
  expect(container.textContent).toContain('Licensed reference')
  expect(container.querySelector('a[data-starter-license]')?.getAttribute('href')).toBe('https://creativecommons.org/licenses/by/4.0/')
  expect(container.querySelector('a[data-starter-source]')?.getAttribute('href')).toBe('https://datashare.ed.ac.uk/handle/10283/3443')
  expect(container.textContent).toContain('University of Edinburgh')
  button(container, 'Add VCTK p225 to my voices').click()
})
it('stays compact with an existing library and uses an existing import without requesting another', async () => {
  const imported: SpeechVoiceProfile = { id: 'a'.repeat(32), name: 'My edited name', consent_confirmed: true, reference_audio_path: '/private/reference.wav', created_at: 'now', updated_at: 'now', starter_voice_id: ids[0] }
  const { container, imports, selections } = await mount([imported])
  expect(container.querySelector('audio')).toBeNull()
  const browse = button(container, 'Browse starter voices')
  expect(browse.getAttribute('aria-expanded')).toBe('false')
  const controlled = container.querySelector('#starter-voice-catalog')
  expect(controlled).not.toBeNull()
  if (!(controlled instanceof HTMLElement)) throw new Error('Missing catalog disclosure target')
  expect(controlled.style.display).toBe('none')
  browse.click(); await settle()
  button(container, 'Use VCTK p225').click(); await settle()
  expect(imports).toEqual([])
  expect(selections).toEqual([imported.id])
  expect(container.textContent).not.toContain('/private/')
})
it('emits an import identifier and pauses previews when the catalog closes', async () => {
  const { container, imports } = await mount()
  button(container, 'Add VCTK p225 to my voices').click(); await settle()
  expect(imports).toEqual(['vctk-p225'])
  const audio = container.querySelector('audio')
  if (!audio) throw new Error('Missing source preview')
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined)
  button(container, 'Hide starter voices').click(); await settle()
  expect(pause).toHaveBeenCalledTimes(1)
  expect(container.querySelector('audio')).toBeNull()
})
it('shows a safe catalog failure and supports retry independent of the parent library', async () => {
  vi.mocked(api.listStarterSpeechVoices).mockRejectedValueOnce(new Error('/private/catalog token=secret')).mockResolvedValue(voices)
  const { container } = await mount()
  expect(container.textContent).toContain('Could not load starter voices')
  expect(container.textContent).not.toContain('token=secret')
  button(container, 'Reload starter voices').click(); await settle()
  expect(container.querySelectorAll('[data-starter-voice]')).toHaveLength(4)
})
it('aborts loading on unmount and ignores a late catalog response', async () => {
  let finish: ((value: StarterSpeechVoice[]) => void) | undefined
  vi.mocked(api.listStarterSpeechVoices).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { container } = await mount()
  const signal = vi.mocked(api.listStarterSpeechVoices).mock.calls[0]?.[0]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  if (!finish) throw new Error('Missing catalog request')
  finish(voices); await settle()
  expect(container.querySelector('audio')).toBeNull()
  expect(api.listStarterSpeechVoices).toHaveBeenCalledTimes(1)
})
it('prevents source previews from overlapping and stops them when Speech is hidden', async () => {
  const { container, active } = await mount()
  const [first, second] = [...container.querySelectorAll('audio')]
  if (!first || !second) throw new Error('Missing starter previews')
  const pauseFirst = vi.spyOn(first, 'pause').mockImplementation(() => undefined)
  const pauseSecond = vi.spyOn(second, 'pause').mockImplementation(() => undefined)
  first.dispatchEvent(new Event('play')); second.dispatchEvent(new Event('play'))
  expect(pauseFirst).toHaveBeenCalledTimes(1)
  active.value = false; await settle()
  expect(pauseSecond).toHaveBeenCalledTimes(1)
})
