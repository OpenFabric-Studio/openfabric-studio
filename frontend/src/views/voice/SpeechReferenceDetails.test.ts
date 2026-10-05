// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import SpeechReferenceDetails from './SpeechReferenceDetails.vue'
import * as api from '../../api/voiceProfiles'
import type { SpeechVoiceProfile } from '../../api/contracts'
import en from '../../locales/en'
vi.mock('../../api/voiceProfiles', async original => ({ ...await original<typeof import('../../api/voiceProfiles')>(), patchSpeechVoiceProfile: vi.fn() }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.resetAllMocks() })
const profile: SpeechVoiceProfile = { id: 'a'.repeat(32), name: 'Reference', consent_confirmed: true, reference_audio_path: '/fixture.wav', notes: 'Recording notes', created_at: 'now', updated_at: 'now' }
async function settle() { for (let i = 0; i < 10; i++) await nextTick() }
it('saves transcript and language independently of notes and ignores feedback from an old selection', async () => {
  let resolve: (value: SpeechVoiceProfile) => void = () => { throw new Error('Not initialized') }
  const request = new Promise<SpeechVoiceProfile>(release => { resolve = release })
  vi.mocked(api.patchSpeechVoiceProfile).mockReturnValue(request)
  const selected = ref(profile)
  app = createApp({ render: () => h(SpeechReferenceDetails, { profile: selected.value }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  const transcript = container.querySelector('textarea')
  const language = container.querySelector('input')
  if (!transcript || !language) throw new Error('Missing reference inputs')
  transcript.value = 'Bonjour tout le monde'; transcript.dispatchEvent(new Event('input'))
  language.value = 'fr'; language.dispatchEvent(new Event('input')); await settle()
  container.querySelector('button')?.click(); await settle()
  expect(api.patchSpeechVoiceProfile).toHaveBeenCalledWith(profile.id, { reference_transcript: 'Bonjour tout le monde', reference_language: 'fr' }, expect.any(AbortSignal))
  selected.value = { ...profile, id: 'b'.repeat(32), name: 'Different reference' }; await settle()
  resolve({ ...profile, reference_transcript: 'Bonjour tout le monde', reference_language: 'fr' }); await settle()
  expect(container.textContent).not.toContain('Reference details saved')
  expect(transcript.value).toBe('')
})
it('keeps the success notice when the parent publishes the saved profile', async () => {
  const selected = ref({ ...profile, reference_transcript: 'Spoken words', reference_language: 'en' })
  vi.mocked(api.patchSpeechVoiceProfile).mockResolvedValue(selected.value)
  app = createApp({ render: () => h(SpeechReferenceDetails, { profile: selected.value, onSaved: updated => { selected.value = { ...selected.value, ...updated, reference_transcript: updated.reference_transcript ?? '', reference_language: updated.reference_language ?? 'en' } } }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  container.querySelector('button')?.click(); await settle()
  expect(container.textContent).toContain('Reference details saved')
})
