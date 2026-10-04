// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import VoiceClonePage from './VoiceClonePage.vue'
import * as api from '../../api/voices'
import * as audiobooksApi from '../../api/audiobooks'
import * as profilesApi from '../../api/voiceProfiles'
import { i18n, setLocale } from '../../i18n'
import { voiceProfile } from './voiceTestFixtures'
vi.mock('../../api/voices', async original => ({ ...await original<typeof import('../../api/voices')>(), createVoice: vi.fn(), startVoiceComparison: vi.fn(), uploadRecordings: vi.fn(), listVoices: vi.fn(), getVoicePreparation: vi.fn(), voiceSeparationOptions: vi.fn(), listVoiceTrialSources: vi.fn(), listVoiceComparisons: vi.fn() }))
vi.mock('../../api/voiceProfiles', async original => ({ ...await original<typeof import('../../api/voiceProfiles')>(), listStarterSpeechVoices: vi.fn().mockResolvedValue([]), importStarterSpeechVoice: vi.fn(), listSpeechVoiceProfiles: vi.fn().mockResolvedValue([]), getSpeechCloneEngine: vi.fn().mockRejectedValue(new Error('Unavailable in test')), createSpeechVoiceProfile: vi.fn(), deleteSpeechVoiceProfile: vi.fn(), startSpeechCloneTrial: vi.fn() }))
vi.mock('../../api/audiobooks', () => ({ __v_isRef: false, listAudiobooks: vi.fn().mockResolvedValue([]), listAudiobookJobs: vi.fn().mockResolvedValue([]), createAudiobook: vi.fn(), retryAudiobook: vi.fn(), audiobookExportUrl: (id: string) => `/api/audiobooks/${id}/export`, audiobookExportFormatUrl: (id: string, format: string) => `/api/audiobooks/${id}/exports/${format}`, audiobookCueUrl: (id: string) => `/api/audiobooks/${id}/exports/cue`, audiobookCollectionUrl: (id: string) => `/api/audiobooks/${id}/exports/collection`, setAudiobookLanguages: vi.fn(), audiobookChapterAudioUrl: (id: string, chapter: number) => `/api/audiobooks/${id}/chapters/${chapter}/audio` }))
let app: App | undefined
beforeEach(() => {
  setLocale('en'); vi.mocked(api.listVoices).mockResolvedValue([voiceProfile()]); vi.mocked(api.getVoicePreparation).mockResolvedValue({ status: 'idle' }); vi.mocked(api.voiceSeparationOptions).mockResolvedValue([]); vi.mocked(api.listVoiceTrialSources).mockResolvedValue([]); vi.mocked(api.listVoiceComparisons).mockResolvedValue([])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllMocks() })
async function settle() { for (let i = 0; i < 12; i++) await nextTick(); await new Promise(resolve => setTimeout(resolve, 0)); await nextTick() }
async function mount(query = '', path = '/voice-clone') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/voice-clone', name: 'voice-clone', component: VoiceClonePage },
    { path: '/audiobooks', name: 'audiobooks', component: VoiceClonePage, props: { audiobooksOnly: true } },
  ] })
  await router.push(`${path}${query}`); await router.isReady()
  app = createApp({ render: () => h(RouterView) }); app.use(i18n).use(router)
  const container = document.createElement('div'); document.body.append(container); app.mount(container); await settle()
  return { container, router }
}
async function click(container: HTMLElement, label: string) {
  const button = container.querySelector(`[aria-label="${label}"]`) ?? [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!(button instanceof HTMLButtonElement)) throw new Error(`Missing ${label}`)
  button.click(); await settle()
}
function activityButton(container: HTMLElement, message: string): HTMLButtonElement {
  const button = [...container.querySelectorAll('button')].find(item => item.textContent?.includes(message))
  if (!button) throw new Error(`Missing activity: ${message}`)
  return button
}
it('opens Audiobook as its own workspace without voice mode tabs', async () => {
  const { container } = await mount('', '/audiobooks')
  expect(container.querySelector('h1')?.textContent).toBe('Audiobook')
  expect(container.querySelector('[aria-label="Search audiobooks"]')).not.toBeNull()
  expect(container.querySelector('[role="tablist"]')).toBeNull()
  expect(container.textContent).toContain('saved speech profile')
})
it('does not fetch singing voices on direct Audiobook entry, even with a voice query', async () => {
  const { container } = await mount('?mode=singing&voice=new&stage=compare', '/audiobooks')
  expect(api.listVoices).not.toHaveBeenCalled()
  expect(container.querySelector('h1')?.textContent).toBe('Audiobook')
  expect(audiobooksApi.listAudiobooks).toHaveBeenCalledTimes(1)
})
it('keeps only Singing and Speech in Voice Clone keyboard navigation', async () => {
  const { container, router } = await mount('?mode=speech')
  expect(container.querySelectorAll('[id^="studio-tab-"]')).toHaveLength(2)
  const speech = container.querySelector('[aria-label="Speech"]')
  if (!(speech instanceof HTMLButtonElement)) throw new Error('Missing Speech tab')
  speech.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await settle()
  expect(router.currentRoute.value.path).toBe('/voice-clone')
  expect(router.currentRoute.value.query.mode).toBe('singing')
  expect(document.activeElement).toBe(container.querySelector('[aria-label="Singing"]'))
})
it('retains singing and audiobook drafts when the sidebar changes routes', async () => {
  const { container, router } = await mount()
  await click(container, 'New voice')
  const singer = container.querySelector('[data-singing-name]')
  if (!(singer instanceof HTMLInputElement)) throw new Error('Missing singing name')
  singer.value = 'Unfinished singer'; singer.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  await router.push('/audiobooks'); await settle(); await click(container, 'New audiobook')
  const title = container.querySelector('[aria-label="Book title"]')
  const chapter = container.querySelector('[aria-label="Chapter 1 text"]')
  if (!(title instanceof HTMLInputElement) || !(chapter instanceof HTMLTextAreaElement)) throw new Error('Missing audiobook draft')
  title.value = 'Unfinished book'; title.dispatchEvent(new Event('input', { bubbles: true }))
  chapter.value = 'Unfinished chapter'; chapter.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  await router.push('/voice-clone'); await settle()
  expect(container.querySelector('[data-singing-name]')).toBe(singer)
  expect(singer.value).toBe('Unfinished singer')
  await router.push('/audiobooks'); await settle()
  expect(container.querySelector('[aria-label="Book title"]')).toBe(title)
  expect(title.value).toBe('Unfinished book')
  expect(chapter.value).toBe('Unfinished chapter')
  expect(api.listVoices).toHaveBeenCalledTimes(1)
  expect(audiobooksApi.listAudiobooks).toHaveBeenCalledTimes(1)
})
it('replaces legacy audiobook URLs without loading Singing or discarding other query values', async () => {
  const { container, router } = await mount('?mode=audiobooks&voice=new&stage=compare&filter=recent#chapter')
  expect(router.currentRoute.value.path).toBe('/audiobooks')
  expect(router.currentRoute.value.query).toEqual({ voice: 'new', stage: 'compare', filter: 'recent' })
  expect(router.currentRoute.value.hash).toBe('#chapter')
  expect(container.querySelector('h1')?.textContent).toBe('Audiobook')
  expect(api.listVoices).not.toHaveBeenCalled()
})
it('returns from audiobook activity to the correct Voice Clone mode and visible focus owner', async () => {
  const { container, router } = await mount('?mode=speech&filter=recent')
  await router.push('/audiobooks?filter=recent'); await settle(); await click(container, 'New audiobook')
  const title = container.querySelector('[aria-label="Book title"]')
  if (!(title instanceof HTMLInputElement)) throw new Error('Missing book title')
  title.focus()
  const activity = activityButton(container, 'View Speech activity')
  activity.focus(); activity.click(); await settle()
  expect(router.currentRoute.value.path).toBe('/voice-clone')
  expect(router.currentRoute.value.query).toEqual({ filter: 'recent', mode: 'speech' })
  expect(document.activeElement).toBe(container.querySelector('[aria-label="Speech"]'))
})
it('navigates to Audiobook activity and moves hidden confirmation focus to its heading', async () => {
  vi.mocked(audiobooksApi.listAudiobooks).mockResolvedValueOnce([{ id: 'b'.repeat(32), title: 'Failed book', profile_id: 'c'.repeat(32), status: 'failed', chapter_count: 1, created_at: 'now', updated_at: 'now' }])
  const { container, router } = await mount('', '/audiobooks')
  await router.push('/voice-clone'); await settle(); await click(container, 'Delete voice')
  const cancel = document.querySelector('[role="dialog"] button')
  if (!(cancel instanceof HTMLButtonElement)) throw new Error('Missing Cancel')
  cancel.focus()
  activityButton(container, 'View Audiobook activity').click(); await settle()
  expect(router.currentRoute.value.path).toBe('/audiobooks')
  expect(router.currentRoute.value.query.mode).toBeUndefined()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
  expect(document.activeElement).toBe(container.querySelector('h1'))
})
it('focuses the audiobook heading when its activity link disappears after navigation', async () => {
  vi.mocked(audiobooksApi.listAudiobooks).mockResolvedValueOnce([{ id: 'b'.repeat(32), title: 'Failed book', profile_id: 'c'.repeat(32), status: 'failed', chapter_count: 1, created_at: 'now', updated_at: 'now' }])
  const { container, router } = await mount('', '/audiobooks')
  await router.push('/voice-clone'); await settle()
  const activity = activityButton(container, 'View Audiobook activity')
  activity.focus(); activity.click(); await settle()
  expect(document.activeElement).toBe(container.querySelector('h1'))
})
it('keeps a pending new singer creation owned without writing queries on the audiobook route', async () => {
  let finish: ((value: Awaited<ReturnType<typeof api.createVoice>>) => void) | undefined
  vi.mocked(api.createVoice).mockReturnValue(new Promise(resolve => { finish = resolve }))
  vi.mocked(api.uploadRecordings).mockResolvedValue({ voice: voiceProfile(), saved: ['first.wav'], skipped: [] })
  const { container, router } = await mount()
  await click(container, 'New voice')
  const picker = container.querySelector('input[type=file]')
  if (!(picker instanceof HTMLInputElement)) throw new Error('Missing source picker')
  Object.defineProperty(picker, 'files', { configurable: true, value: [new File(['audio'], 'first.wav')] })
  picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  const signal = vi.mocked(api.createVoice).mock.calls[0]?.[1]
  await router.push('/audiobooks?filter=recent'); await settle()
  expect(signal?.aborted).toBe(false)
  expect(container.textContent).toContain('Saving source files')
  if (!finish) throw new Error('Missing singer creation completion')
  finish(voiceProfile()); await settle()
  expect(api.uploadRecordings).toHaveBeenCalledTimes(1)
  expect(router.currentRoute.value.fullPath).toBe('/audiobooks?filter=recent')
  await router.push('/voice-clone'); await settle()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('My voice')
})
it('does not replay a stale new-singer query when returning from Audiobook activity', async () => {
  vi.mocked(audiobooksApi.listAudiobooks).mockResolvedValueOnce([{ id: 'b'.repeat(32), title: 'Failed book', profile_id: 'c'.repeat(32), status: 'failed', chapter_count: 1, created_at: 'now', updated_at: 'now' }])
  let finishCreation: ((value: Awaited<ReturnType<typeof api.createVoice>>) => void) | undefined
  let finishUpload: ((value: Awaited<ReturnType<typeof api.uploadRecordings>>) => void) | undefined
  vi.mocked(api.createVoice).mockReturnValue(new Promise(resolve => { finishCreation = resolve }))
  vi.mocked(api.uploadRecordings).mockReturnValue(new Promise(resolve => { finishUpload = resolve }))
  const { container, router } = await mount('?filter=recent', '/audiobooks')
  await router.push('/voice-clone?filter=recent'); await settle(); await click(container, 'New voice')
  const picker = container.querySelector('input[type=file]')
  if (!(picker instanceof HTMLInputElement)) throw new Error('Missing source picker')
  Object.defineProperty(picker, 'files', { configurable: true, value: [new File(['audio'], 'first.wav')] })
  picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  activityButton(container, 'View Audiobook activity').click(); await settle()
  expect(router.currentRoute.value.fullPath).toBe('/audiobooks?filter=recent')
  if (!finishCreation) throw new Error('Missing singer creation completion')
  finishCreation(voiceProfile()); await settle()
  const signal = vi.mocked(api.uploadRecordings).mock.calls[0]?.[2]
  activityButton(container, 'View Singing activity').click(); await settle()
  expect(router.currentRoute.value.path).toBe('/voice-clone')
  expect(container.querySelector('[data-singing-name]')).toBeNull()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('My voice')
  expect(signal?.aborted).toBe(false)
  if (!finishUpload) throw new Error('Missing source upload completion')
  finishUpload({ voice: voiceProfile(), saved: ['first.wav'], skipped: [] }); await settle()
})
it('retains a selected singer and training stage across a bare Audiobook route round trip', async () => {
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other singer' }
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile(), second])
  const { container, router } = await mount(`?voice=${second.id}&stage=build`)
  await router.push('/audiobooks'); await settle(); await router.push('/voice-clone'); await settle()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('Other singer')
  expect(container.querySelector('[aria-label="Train"]')?.getAttribute('aria-selected')).toBe('true')
})
it('retains the singer and training stage when switching voice modes after a bare Audiobook round trip', async () => {
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other singer' }
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile(), second])
  const { container, router } = await mount(`?voice=${second.id}&stage=build`)
  await router.push('/audiobooks'); await settle(); await router.push('/voice-clone'); await settle()
  await click(container, 'Speech'); await click(container, 'Singing')
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('Other singer')
  expect(container.querySelector('[aria-label="Train"]')?.getAttribute('aria-selected')).toBe('true')
})
it('honors an explicit singer bookmark when leaving Speech after a bare Audiobook round trip', async () => {
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other singer' }
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile(), second])
  const { container, router } = await mount(`?voice=${second.id}&stage=build`)
  await router.push('/audiobooks'); await settle(); await router.push('/voice-clone'); await settle(); await click(container, 'Speech')
  await router.push(`/voice-clone?mode=singing&voice=${voiceProfile().id}&stage=files`); await settle()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('My voice')
  expect(container.querySelector('[aria-label="Sources"]')?.getAttribute('aria-selected')).toBe('true')
})
it.each(['', '?voice=00000000000000000000000000000000&stage=compare'])('keeps the requested singer and stage when its first library response arrives on Audiobook %s', async audiobookQuery => {
  const first = voiceProfile()
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other singer' }
  let finish: ((value: Awaited<ReturnType<typeof api.listVoices>>) => void) | undefined
  vi.mocked(api.listVoices).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { container, router } = await mount(`?voice=${second.id}&stage=build`)
  await router.push(`/audiobooks${audiobookQuery}`); await settle()
  if (!finish) throw new Error('Missing singing library response')
  finish([first, second]); await settle()
  await router.push('/voice-clone'); await settle()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('Other singer')
  expect(container.querySelector('[aria-label="Train"]')?.getAttribute('aria-selected')).toBe('true')
})
it('keeps explicit singing bookmark changes made while its first library response is pending', async () => {
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other singer' }
  let finish: ((value: Awaited<ReturnType<typeof api.listVoices>>) => void) | undefined
  vi.mocked(api.listVoices).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { container, router } = await mount()
  await router.push(`/voice-clone?voice=${second.id}&stage=build`); await settle()
  await router.push('/audiobooks'); await settle()
  if (!finish) throw new Error('Missing singing library response')
  finish([voiceProfile(), second]); await settle()
  await router.push('/voice-clone'); await settle()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('Other singer')
  expect(container.querySelector('[aria-label="Train"]')?.getAttribute('aria-selected')).toBe('true')
})
it('ignores audiobook stage queries when a hidden new singer creation completes', async () => {
  const created = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Created singer' }
  let finish: ((value: Awaited<ReturnType<typeof api.createVoice>>) => void) | undefined
  vi.mocked(api.createVoice).mockReturnValue(new Promise(resolve => { finish = resolve }))
  vi.mocked(api.uploadRecordings).mockResolvedValue({ voice: created, saved: ['first.wav'], skipped: [] })
  const { container, router } = await mount()
  await click(container, 'New voice')
  const picker = container.querySelector('input[type=file]')
  if (!(picker instanceof HTMLInputElement)) throw new Error('Missing source picker')
  Object.defineProperty(picker, 'files', { configurable: true, value: [new File(['audio'], 'first.wav')] })
  picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  await router.push(`/audiobooks?voice=${created.id}&stage=compare`); await settle()
  if (!finish) throw new Error('Missing singer creation response')
  finish(created); await settle()
  await router.push('/voice-clone'); await settle()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('Created singer')
  expect(container.querySelector('[aria-label="Sources"]')?.getAttribute('aria-selected')).toBe('true')
})
it('replaces the legacy history entry so Back returns to the earlier Speech view', async () => {
  const { container, router } = await mount('?mode=speech')
  await router.push('/voice-clone?mode=audiobooks&filter=recent'); await settle()
  expect(router.currentRoute.value.path).toBe('/audiobooks')
  router.back(); await settle()
  expect(router.currentRoute.value.fullPath).toBe('/voice-clone?mode=speech')
  expect(container.querySelector('[aria-label="Speech"]')?.getAttribute('aria-selected')).toBe('true')
  expect(api.listVoices).not.toHaveBeenCalled()
})
it('pauses audiobook chapter audio when Voice Clone becomes visible', async () => {
  const book = { id: 'b'.repeat(32), title: 'Saved book', profile_id: 'c'.repeat(32), status: 'done' as const, chapter_count: 1, created_at: 'now', updated_at: 'now' }
  vi.mocked(audiobooksApi.listAudiobooks).mockResolvedValueOnce([book])
  vi.mocked(audiobooksApi.listAudiobookJobs).mockResolvedValueOnce([{ id: 'chapter', book_id: book.id, chapter_index: 0, chapter_title: 'First chapter', status: 'done', created_at: 'now', updated_at: 'now' }])
  const { container, router } = await mount('', '/audiobooks')
  const audio = container.querySelector('audio')
  if (!(audio instanceof HTMLAudioElement)) throw new Error('Missing chapter audio')
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined)
  await router.push('/voice-clone?mode=speech'); await settle()
  expect(pause).toHaveBeenCalledTimes(1)
})
it('keeps a pending speech action owned across routes and links back to Speech', async () => {
  const narrator = { id: 'c'.repeat(32), name: 'Narrator', consent_confirmed: true, reference_audio_path: '/mock/reference.wav', created_at: 'now', updated_at: 'now' }
  vi.mocked(profilesApi.listSpeechVoiceProfiles).mockResolvedValueOnce([narrator])
  let finish: ((value: Awaited<ReturnType<typeof profilesApi.startSpeechCloneTrial>>) => void) | undefined
  vi.mocked(profilesApi.startSpeechCloneTrial).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { container, router } = await mount('?mode=speech')
  const text = container.querySelector('[aria-label="Text to speak"]')
  if (!(text instanceof HTMLTextAreaElement)) throw new Error('Missing speech text')
  text.value = 'A voice trial'; text.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  const form = container.querySelector('form[aria-label="Speech synthesis"]')
  if (!(form instanceof HTMLFormElement)) throw new Error('Missing speech synthesis form')
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
  await router.push('/audiobooks'); await settle()
  expect(vi.mocked(profilesApi.startSpeechCloneTrial).mock.calls[0]?.[2]?.aborted).toBe(false)
  activityButton(container, 'Generating speech trial').click(); await settle()
  expect(router.currentRoute.value.path).toBe('/voice-clone')
  expect(router.currentRoute.value.query.mode).toBe('speech')
  expect(container.querySelector('[aria-label="Text to speak"]')).toBe(text)
  if (!finish) throw new Error('Missing speech completion')
  finish({ trial_id: 'trial', profile_id: narrator.id, status: 'completed', engine: 'gpt-sovits', detail: 'Speech trial completed.' }); await settle()
})
it('keeps a new singing voice draft when switching modes', async () => {
  const { container } = await mount()
  await click(container, 'New voice')
  const name = container.querySelector('[data-singing-name]')
  if (!(name instanceof HTMLInputElement)) throw new Error('Missing singing name')
  name.value = 'Unfinished singer'; name.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  await click(container, 'Speech'); await click(container, 'Singing')
  expect(container.querySelector('[data-singing-name]')).toBe(name)
  expect(name.value).toBe('Unfinished singer')
})
it('validates mode and stage queries and restores deliberate navigation with Back', async () => {
  const { container, router } = await mount('?mode=unknown&stage=oops&voice=missing')
  expect(container.querySelector('[aria-label="Singing"]')?.getAttribute('aria-selected')).toBe('true')
  expect(container.querySelector('[aria-label="Sources"]')?.getAttribute('aria-selected')).toBe('true')
  await click(container, 'Train'); await click(container, 'Speech')
  expect(router.currentRoute.value.query.mode).toBe('speech')
  router.back(); await new Promise(resolve => setTimeout(resolve, 0)); await settle()
  expect(container.querySelector('[aria-label="Singing"]')?.getAttribute('aria-selected')).toBe('true')
  expect(container.querySelector('[aria-label="Train"]')?.getAttribute('aria-selected')).toBe('true')
})
it('opens an existing voice and requested stage from a bookmarked route', async () => {
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other singer' }
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile(), second])
  const { container } = await mount(`?voice=${second.id}&stage=coverage`)
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('Other singer')
  expect(container.querySelector('[aria-label="Coverage"]')?.getAttribute('aria-selected')).toBe('true')
})

it('shows a source upload still running after changing modes', async () => {
  let finish: ((value: Awaited<ReturnType<typeof api.uploadRecordings>>) => void) | undefined
  vi.mocked(api.uploadRecordings).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { container } = await mount()
  const picker = container.querySelector('input[type=file]')
  if (!(picker instanceof HTMLInputElement)) throw new Error('Missing source picker')
  Object.defineProperty(picker, 'files', { configurable: true, value: [new File(['audio'], 'source.wav', { type: 'audio/wav' })] })
  picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  await click(container, 'Speech')
  const activity = [...container.querySelectorAll('button')].find(item => item.textContent?.includes('Saving source files'))
  expect(activity).toBeDefined()
  if (!finish) throw new Error('Missing upload completion')
  finish({ voice: voiceProfile(), saved: ['source.wav'], skipped: [] }); await settle()
  expect([...container.querySelectorAll('button')].some(item => item.textContent?.includes('Saving source files'))).toBe(false)
})

it('replaces the new-voice bookmark with the created voice after uploading', async () => {
  vi.mocked(api.createVoice).mockResolvedValue(voiceProfile())
  vi.mocked(api.uploadRecordings).mockResolvedValue({ voice: voiceProfile(), saved: ['first.wav'], skipped: [] })
  const { container, router } = await mount()
  await click(container, 'New voice')
  const picker = container.querySelector('input[type=file]')
  if (!(picker instanceof HTMLInputElement)) throw new Error('Missing source picker')
  Object.defineProperty(picker, 'files', { configurable: true, value: [new File(['audio'], 'first.wav')] })
  picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(router.currentRoute.value.query.voice).toBe(voiceProfile().id)
})
it('restores Sources when going Back to an initial route with no stage', async () => {
  const { container, router } = await mount()
  await click(container, 'Train'); router.back(); await settle()
  expect(container.querySelector('[aria-label="Sources"]')?.getAttribute('aria-selected')).toBe('true')
})
it('opens a newly selected voice at its running coverage job', async () => {
  const second = { ...voiceProfile('abcdef1234567890abcdef1234567890'), name: 'Other singer', usable: true, status: 'ready' as const }
  vi.mocked(api.listVoices).mockResolvedValue([voiceProfile(), second])
  vi.mocked(api.getVoicePreparation).mockImplementation(async id => id === second.id ? { status: 'running', operation: 'coverage' } : { status: 'idle' })
  const { container } = await mount()
  const pick = [...container.querySelectorAll('button')].find(item => item.textContent?.includes('Other singer'))
  if (!pick) throw new Error('Missing other singer')
  pick.click(); await settle()
  expect(container.querySelector('[aria-label="Coverage"]')?.getAttribute('aria-selected')).toBe('true')
})
it('keeps mode-tab focus when a singing confirmation closes during mode navigation', async () => {
  const { container, router } = await mount()
  const opener = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === 'Delete voice')
  if (!opener) throw new Error('Missing delete opener')
  opener.focus(); opener.click(); await settle()
  const tab = container.querySelector('[aria-label="Speech"]')
  if (!(tab instanceof HTMLButtonElement)) throw new Error('Missing Speech tab')
  tab.focus(); await router.push({ query: { mode: 'speech' } }); await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
  expect(document.activeElement).toBe(tab)
})
it('announces a pending comparison after switching modes', async () => {
  const singer = { ...voiceProfile(), usable: true, status: 'ready' as const }
  vi.mocked(api.listVoices).mockResolvedValue([singer])
  vi.mocked(api.listVoiceTrialSources).mockResolvedValue([{ id: 'abcdef1234567890abcdef1234567890', filename: 'held-out.wav', duration_sec: 20 }])
  vi.mocked(api.voiceSeparationOptions).mockResolvedValue([{ id: 'fast', available: true }])
  let complete: ((value: Awaited<ReturnType<typeof api.startVoiceComparison>>) => void) | undefined
  vi.mocked(api.startVoiceComparison).mockReturnValue(new Promise(resolve => { complete = resolve }))
  const { container } = await mount()
  await click(container, 'Compare'); await click(container, 'Run comparison'); await click(container, 'Speech')
  expect(container.textContent).toContain('Singing comparison in progress')
  if (!complete) throw new Error('Missing comparison response')
  complete({ id: 'comparison-1', voice_id: singer.id, status: 'done', source_filename: 'held-out.wav', request: { source_id: 'abcdef1234567890abcdef1234567890', model_ids: ['base'] }, trials: [] }); await settle()
})

it('pauses the published reference when Singing becomes hidden', async () => {
  vi.mocked(api.listVoices).mockResolvedValue([{ ...voiceProfile(), usable: true, status: 'ready' }])
  const { container } = await mount()
  const audio = container.querySelector('audio')
  if (!audio) throw new Error('Missing published reference player')
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined)
  await click(container, 'Speech')
  expect(pause).toHaveBeenCalledTimes(1)
})

it('restores the library selection when going Back from a new voice draft', async () => {
  const { container, router } = await mount()
  await click(container, 'New voice'); router.back(); await settle()
  expect(container.querySelector('[data-singing-heading]')?.textContent).toBe('My voice')
})
it('moves dialog focus to the visible mode tab when Back hides Singing', async () => {
  const { container, router } = await mount('?mode=speech')
  await click(container, 'Singing'); await click(container, 'Delete voice')
  const cancel = document.querySelector('[role="dialog"] button')
  if (!(cancel instanceof HTMLButtonElement)) throw new Error('Missing Cancel')
  cancel.focus(); router.back(); await settle()
  expect(document.activeElement).toBe(container.querySelector('[aria-label="Speech"]'))
})

it('does not let keyboard navigation focus a replacement workspace after teardown', async () => {
  const { container } = await mount()
  const tab = container.querySelector('[aria-label="Singing"]')
  if (!(tab instanceof HTMLButtonElement)) throw new Error('Missing Singing tab')
  tab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
  app?.unmount(); app = undefined
  const replacement = document.createElement('button'); replacement.id = 'studio-tab-speech'; document.body.append(replacement)
  await settle()
  expect(document.activeElement).not.toBe(replacement)
})
