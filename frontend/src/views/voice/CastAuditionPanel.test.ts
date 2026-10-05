// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import CastAuditionPanel from './CastAuditionPanel.vue'
import * as api from '../../api/audiobookWorkflow'
import type { AudiobookAudition, CreateAudiobookRequest } from '../../api/contracts'
import en from '../../locales/en'
vi.mock('../../api/audiobookWorkflow', async original => ({ ...await original<typeof import('../../api/audiobookWorkflow')>(), __v_isRef: false, auditionDraft: vi.fn(), getAudition: vi.fn(), cancelAudition: vi.fn() }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.resetAllMocks() })
async function settle() { for (let i = 0; i < 10; i++) await nextTick() }
const request: CreateAudiobookRequest = { title: 'Scene', profile_id: 'a'.repeat(32), language: 'ja', chapters: [{ text: 'Alice: Hello there', title: 'Scene' }], cast: [{ name: 'Alice', profile_id: 'b'.repeat(32) }], pronunciations: [{ written: 'there', spoken: 'their' }] }
const audition: AudiobookAudition = { id: 'c'.repeat(32), mode: 'cast', status: 'done', created_at: 'now', updated_at: 'now', clips: [{ index: 0, speaker: 'Alice', profile_id: 'b'.repeat(32), text: 'Hello their', language: 'ja', status: 'done', audio_url: `/api/audiobooks/auditions/${'c'.repeat(32)}/clips/0/audio` }] }
it('passes cast/language/pronunciations and discards late audition audio when draft inputs change', async () => {
  let resolve: (value: AudiobookAudition) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.auditionDraft).mockReturnValue(new Promise(release => { resolve = release }))
  const draft = ref(request)
  app = createApp({ render: () => h(CastAuditionPanel, { draft: draft.value, active: true }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  const button = [...container.querySelectorAll('button')].find(item => item.textContent === 'Audition cast')
  button?.click(); await settle()
  expect(api.auditionDraft).toHaveBeenCalledWith({ ...request, mode: 'cast', max_chars: 600, chapter_index: 0 }, expect.any(AbortSignal))
  draft.value = { ...request, chapters: [{ text: 'Alice: Changed line' }] }; await settle()
  resolve(audition); await settle()
  expect(container.querySelector('audio')).toBeNull()
  expect(container.textContent).not.toContain('Hello their')
})
it('plays each representative voice and pauses it when hidden', async () => {
  vi.mocked(api.auditionDraft).mockResolvedValue(audition)
  const active = ref(true)
  app = createApp({ render: () => h(CastAuditionPanel, { draft: request, active: active.value }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  ;[...container.querySelectorAll('button')].find(item => item.textContent === 'Audition cast')?.click(); await settle()
  const audio = container.querySelector('audio'); if (!audio) throw new Error('Missing cast audio')
  expect(container.textContent).toContain('Alice'); expect(container.textContent).toContain('Hello their')
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined)
  active.value = false; await settle(); expect(pause).toHaveBeenCalled()
})
