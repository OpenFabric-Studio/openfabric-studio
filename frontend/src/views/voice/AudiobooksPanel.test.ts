// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import AudiobooksPanel from './AudiobooksPanel.vue'
import * as api from '../../api/audiobooks'
import * as profilesApi from '../../api/voiceProfiles'
import type { AudiobookBook, AudiobookCreateResponse, AudiobookJob } from '../../api/audiobooks'
import type { SpeechVoiceProfile } from '../../api/voiceProfiles'
import en from '../../locales/en'
import { audiobookWorkspaceEn } from '../../locales/audiobookWorkspace'

vi.mock('../../api/audiobooks', async (original) => ({ ...await original<typeof import('../../api/audiobooks')>(),
  __v_isRef: false,
  listAudiobooks: vi.fn(), listAudiobookJobs: vi.fn(), createAudiobook: vi.fn(), retryAudiobook: vi.fn(),
}))
vi.mock('../../api/voiceProfiles', () => ({ listSpeechVoiceProfiles: vi.fn() }))

let app: App | undefined
let hidden = ref(false)
const activities: string[] = []
const profile: SpeechVoiceProfile = { id: 'c'.repeat(32), name: 'Narrator', consent_confirmed: true, reference_audio_path: '/private/reference.wav', created_at: 'now', updated_at: 'now' }
function book(id = 'a'.repeat(32), title = 'First book', status: AudiobookBook['status'] = 'done'): AudiobookBook {
  return { id, title, status, profile_id: profile.id, chapter_count: 1, created_at: 'now', updated_at: 'now' }
}
function job(value: AudiobookBook, status: AudiobookJob['status'] = 'done'): AudiobookJob {
  return { id: value.id, book_id: value.id, chapter_index: 0, chapter_title: `${value.title} chapter`, status, created_at: 'now', updated_at: 'now' }
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>((release) => { resolve = release })
  return { promise, resolve }
}
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); activities.length = 0; hidden = ref(false)
  vi.mocked(api.listAudiobooks).mockResolvedValue([book()])
  vi.mocked(api.listAudiobookJobs).mockImplementation(async id => [job(book(id, id === 'b'.repeat(32) ? 'Second book' : 'First book'))])
  vi.mocked(profilesApi.listSpeechVoiceProfiles).mockResolvedValue([profile])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let i = 0; i < 10; i++) await nextTick() }
async function mount() {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en: { ...en, audiobookWorkspace: audiobookWorkspaceEn } } })
  app = createApp({ render: () => h(AudiobooksPanel, { active: !hidden.value, style: { display: hidden.value ? 'none' : '' }, onActivity: (message: string) => activities.push(message) }) }).use(i18n)
  const container = document.body.appendChild(document.createElement('div'))
  app.mount(container); await settle(); return container
}
function button(container: HTMLElement, text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === text || item.getAttribute('aria-label') === text)
  if (!found) throw new Error(`Missing button: ${text}`)
  return found
}
async function click(container: HTMLElement, text: string) { button(container, text).click(); await settle() }
function field(container: HTMLElement, label: string): HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement {
  const found = container.querySelector(`[aria-label="${label}"]`)
  if (!(found instanceof HTMLInputElement || found instanceof HTMLSelectElement || found instanceof HTMLTextAreaElement)) throw new Error(`Missing field: ${label}`)
  return found
}
async function change(container: HTMLElement, label: string, value: string) {
  const element = field(container, label); element.value = value
  element.dispatchEvent(new Event('input', { bubbles: true })); element.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
async function selectBook(container: HTMLElement, id: string) {
  const found = container.querySelector<HTMLButtonElement>(`[data-select-book="${id}"]`)
  if (!found) throw new Error('Missing saved book')
  found.click(); await settle()
}
function submit(container: HTMLElement) {
  const form = container.querySelector('form')
  if (!form) throw new Error('Missing book draft')
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
}
async function draft(container: HTMLElement) {
  await click(container, 'New audiobook')
  await change(container, 'Book title', 'Draft book')
  await change(container, 'Chapter 1 text', 'Draft chapter text')
}

it('opens on saved chapters and reveals creation on demand beside a searchable library', async () => {
  const container = await mount()
  expect(container.querySelector('form')).toBeNull()
  expect(container.querySelector('[data-book-editor]')?.textContent).toContain('First book chapter')
  expect(container.querySelector(`[data-select-book="${book().id}"]`)?.getAttribute('aria-current')).toBe('true')
  await change(container, 'Search audiobooks', ' no match ')
  expect(container.querySelectorAll('[data-select-book]')).toHaveLength(0)
  await click(container, 'New audiobook')
  expect(container.querySelector('form')).not.toBeNull()
})

it('retains narrator and chapter drafts after hiding the mode or returning to saved books', async () => {
  const otherProfile = { ...profile, id: 'd'.repeat(32), name: 'Other narrator' }
  vi.mocked(profilesApi.listSpeechVoiceProfiles).mockResolvedValue([profile, otherProfile])
  const container = await mount(); await draft(container)
  await change(container, 'Narrator', otherProfile.id)
  await change(container, 'Chapter 1 title', 'Draft heading')
  hidden.value = true; await settle(); hidden.value = false; await settle()
  await click(container, 'Back to books'); await click(container, 'New audiobook')
  expect(field(container, 'Book title').value).toBe('Draft book')
  expect(field(container, 'Narrator').value).toBe(otherProfile.id)
  expect(field(container, 'Chapter 1 title').value).toBe('Draft heading')
  expect(field(container, 'Chapter 1 text').value).toBe('Draft chapter text')
})

it('owns a pending creation and rejects duplicate submissions even when submit is dispatched directly', async () => {
  const request = deferred<AudiobookCreateResponse>(); vi.mocked(api.createAudiobook).mockReturnValue(request.promise)
  const container = await mount(); await draft(container)
  submit(container); await settle(); submit(container); await settle()
  expect(api.createAudiobook).toHaveBeenCalledTimes(1)
  expect(api.createAudiobook).toHaveBeenCalledWith({ title: 'Draft book', profile_id: profile.id, chapters: [{ title: 'Chapter 1', text: 'Draft chapter text' }] }, expect.any(AbortSignal))
  expect(activities.some(message => message.includes('Creating'))).toBe(true)
  request.resolve({ book: book('e'.repeat(32), 'Draft book', 'queued'), jobs: [] }); await settle()
  expect(container.querySelector('[data-book-editor]')?.textContent).toContain('Draft book')
})

it('ignores an old jobs response after selecting another book and aborts its request', async () => {
  const first = book(), second = book('b'.repeat(32), 'Second book')
  const request = deferred<AudiobookJob[]>()
  vi.mocked(api.listAudiobooks).mockResolvedValue([first, second])
  vi.mocked(api.listAudiobookJobs).mockReturnValueOnce(request.promise).mockResolvedValue([job(second)])
  const container = await mount(); await selectBook(container, second.id)
  expect(vi.mocked(api.listAudiobookJobs).mock.calls[0]?.[1]?.aborted).toBe(true)
  request.resolve([job(first)]); await settle()
  expect(container.querySelector('[data-book-editor]')?.textContent).toContain('Second book chapter')
  expect(container.querySelector('[data-book-editor]')?.textContent).not.toContain('First book chapter')
})

it('keeps retry owned while pending and does not select its book after the user selects another', async () => {
  const first = book(undefined, undefined, 'failed'), second = book('b'.repeat(32), 'Second book')
  vi.mocked(api.listAudiobooks).mockResolvedValue([first, second])
  const request = deferred<AudiobookBook>(); vi.mocked(api.retryAudiobook).mockReturnValue(request.promise)
  const container = await mount()
  await click(container, 'Retry failed chapters'); button(container, 'Retry failed chapters').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
  expect(api.retryAudiobook).toHaveBeenCalledTimes(1)
  await selectBook(container, second.id)
  request.resolve({ ...first, status: 'queued' }); await settle()
  expect(container.querySelector('[data-book-editor]')?.textContent).toContain('Second book')
  expect(container.querySelector('[data-book-editor]')?.textContent).not.toContain('First book chapter')
})

it('serializes background polling and aborts it on teardown without restarting from a late result', async () => {
  const active = book(undefined, undefined, 'running')
  const request = deferred<AudiobookBook[]>()
  vi.mocked(api.listAudiobooks).mockResolvedValueOnce([active]).mockReturnValue(request.promise)
  const container = await mount()
  expect(container.textContent).toContain('First book')
  await vi.advanceTimersByTimeAsync(10_000)
  expect(api.listAudiobooks).toHaveBeenCalledTimes(2)
  app?.unmount(); app = undefined
  expect(vi.mocked(api.listAudiobooks).mock.calls[1]?.[0]?.aborted).toBe(true)
  request.resolve([active]); await settle(); await vi.advanceTimersByTimeAsync(10_000)
  expect(api.listAudiobooks).toHaveBeenCalledTimes(2)
})

it('aborts a pending creation on unmount and never resumes polling from its completion', async () => {
  const request = deferred<AudiobookCreateResponse>(); vi.mocked(api.createAudiobook).mockReturnValue(request.promise)
  const container = await mount(); await draft(container); submit(container); await settle()
  const signal = vi.mocked(api.createAudiobook).mock.calls[0]?.[1]
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  request.resolve({ book: book(undefined, undefined, 'queued'), jobs: [] }); await settle(); await vi.advanceTimersByTimeAsync(10_000)
  expect(api.listAudiobooks).toHaveBeenCalledTimes(1)
})

it('uses the actual chapter audio route and only exports the selected completed book', async () => {
  const container = await mount()
  expect(container.querySelector('audio')?.getAttribute('src')).toBe(api.audiobookChapterAudioUrl(book().id, 0))
  expect(container.querySelector('[data-export-book]')?.getAttribute('href')).toBe(api.audiobookExportUrl(book().id))
  expect(container.textContent).not.toContain('/private/reference.wav')
})

it('shows safe failures instead of rendering raw backend details or exception messages', async () => {
  vi.mocked(api.listAudiobookJobs).mockResolvedValue([{ ...job(book(), 'failed'), detail: 'chapter_synth_error: token=secret /private/output.wav' }])
  const container = await mount()
  expect(container.textContent).not.toContain('token=secret')
  expect(container.textContent).not.toContain('/private/output.wav')
  await draft(container)
  vi.mocked(api.createAudiobook).mockRejectedValue(new Error('raw secret stack'))
  submit(container); await settle()
  expect(container.textContent).toContain('Could not create the audiobook')
  expect(container.textContent).not.toContain('raw secret stack')
})

it('keeps creation available after a superseded chapter request ignores abort', async () => {
  const first = book(), second = book('b'.repeat(32), 'Second book')
  const request = deferred<AudiobookJob[]>()
  vi.mocked(api.listAudiobooks).mockResolvedValue([first, second])
  vi.mocked(api.listAudiobookJobs).mockReturnValueOnce(request.promise).mockResolvedValue([job(second)])
  const container = await mount(); await selectBook(container, second.id)
  expect(button(container, 'New audiobook').disabled).toBe(false)
  await draft(container)
  expect(field(container, 'Book title').value).toBe('Draft book')
  request.resolve([job(first)]); await settle()
})

it('announces a selected failed book to the hidden-mode activity banner', async () => {
  vi.mocked(api.listAudiobooks).mockResolvedValue([book(undefined, undefined, 'failed')])
  await mount()
  expect(activities.at(-1)).toContain('failed')
})

it('releases retry ownership when a superseded chapter refresh ignores abort', async () => {
  const first = book(undefined, undefined, 'failed'), second = book('b'.repeat(32), 'Second book', 'failed')
  const refresh = deferred<AudiobookJob[]>()
  vi.mocked(api.listAudiobooks).mockResolvedValue([first, second])
  vi.mocked(api.listAudiobookJobs).mockResolvedValueOnce([job(first, 'failed')]).mockReturnValueOnce(refresh.promise).mockResolvedValue([job(second, 'failed')])
  vi.mocked(api.retryAudiobook).mockResolvedValue({ ...first, status: 'queued' })
  const container = await mount(); await click(container, 'Retry failed chapters'); await selectBook(container, second.id)
  expect(button(container, 'Retry failed chapters').disabled).toBe(false)
  refresh.resolve([job(first, 'queued')]); await settle()
})

it('clears chapter-loading errors when a new book replaces the previous selection', async () => {
  vi.mocked(api.listAudiobookJobs).mockRejectedValue(new Error('private error'))
  vi.mocked(api.createAudiobook).mockResolvedValue({ book: book('e'.repeat(32), 'Draft book', 'queued'), jobs: [] })
  const container = await mount()
  expect(container.textContent).toContain('Could not load chapters')
  await draft(container); submit(container); await settle()
  expect(container.querySelector('[data-book-editor]')?.textContent).not.toContain('Could not load chapters')
})

it('keeps a newly selected saved book when an in-flight creation finishes', async () => {
  const first = book(), second = book('b'.repeat(32), 'Second book')
  const request = deferred<AudiobookCreateResponse>()
  vi.mocked(api.listAudiobooks).mockResolvedValue([first, second])
  vi.mocked(api.createAudiobook).mockReturnValue(request.promise)
  const container = await mount(); await draft(container); submit(container); await settle()
  await selectBook(container, second.id)
  request.resolve({ book: book('e'.repeat(32), 'Draft book', 'queued'), jobs: [] }); await settle()
  expect(container.querySelector('[data-book-editor]')?.textContent).toContain('Second book chapter')
  expect(container.querySelector('[data-book-editor]')?.textContent).not.toContain('Draft book')
  expect(container.querySelector(`[data-select-book="${'e'.repeat(32)}"]`)).not.toBeNull()
})

it('rejects a stale polled chapter response after selecting a different book', async () => {
  const first = book(undefined, undefined, 'running'), second = book('b'.repeat(32), 'Second book')
  const request = deferred<AudiobookJob[]>()
  vi.mocked(api.listAudiobooks).mockResolvedValue([first, second])
  vi.mocked(api.listAudiobookJobs).mockResolvedValueOnce([job(first, 'running')]).mockReturnValueOnce(request.promise).mockResolvedValue([job(second)])
  const container = await mount(); await vi.advanceTimersByTimeAsync(2000)
  await selectBook(container, second.id)
  expect(vi.mocked(api.listAudiobookJobs).mock.calls[1]?.[1]?.aborted).toBe(true)
  request.resolve([job(first)]); await settle()
  expect(container.querySelector('[data-book-editor]')?.textContent).toContain('Second book chapter')
  expect(container.querySelector('[data-book-editor]')?.textContent).not.toContain('First book chapter')
})

it('keeps nonempty chapter drafts instead of silently dropping empty chapters on submission', async () => {
  const container = await mount(); await draft(container); await click(container, 'Add chapter')
  submit(container); await settle()
  expect(api.createAudiobook).not.toHaveBeenCalled()
  expect(container.textContent).toContain('Enter text for every chapter')
  await click(container, 'Remove chapter 2')
  expect(field(container, 'Chapter 1 text').value).toBe('Draft chapter text')
  expect(container.querySelectorAll('textarea')).toHaveLength(1)
})

it('identifies mock chapter audio as a silent placeholder without exposing implementation details', async () => {
  vi.mocked(api.listAudiobookJobs).mockResolvedValue([{ ...job(book()), detail: 'Dry-run speech clone wrote a silent placeholder WAV (OPENFABRIC_SPEECH_CLONE_MOCK).' }])
  const container = await mount()
  expect(container.textContent).toContain('silent placeholder from the mock speech engine')
  expect(container.textContent).not.toContain('OPENFABRIC_SPEECH_CLONE_MOCK')
})

it('keeps focus in the newly selected mode when delayed creation focus completes while hidden', async () => {
  const container = await mount()
  button(container, 'New audiobook').click()
  hidden.value = true
  const otherMode = document.body.appendChild(document.createElement('button'))
  otherMode.focus(); await settle()
  expect(document.activeElement).toBe(otherMode)
})

it('restores focus to New audiobook when closing creation', async () => {
  const container = await mount(); await click(container, 'New audiobook')
  expect(document.activeElement).toBe(field(container, 'Book title'))
  await click(container, 'Back to books')
  expect(document.activeElement).toBe(button(container, 'New audiobook'))
})

it('pauses chapter playback when the workspace is hidden', async () => {
  const container = await mount()
  const audio = container.querySelector('audio')
  if (!audio) throw new Error('Missing chapter player')
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined)
  hidden.value = true; await settle()
  expect(pause).toHaveBeenCalledTimes(1)
})

it('refreshes narrators after a Speech profile is added while retaining the existing draft', async () => {
  const container = await mount(); await draft(container)
  await change(container, 'Chapter 1 title', 'Keep heading')
  await click(container, 'Back to books')
  vi.mocked(profilesApi.listSpeechVoiceProfiles).mockResolvedValue([profile, { ...profile, id: 'd'.repeat(32), name: 'New Speech narrator' }])
  await click(container, 'New audiobook')
  expect(field(container, 'Narrator').textContent).toContain('New Speech narrator')
  expect(field(container, 'Narrator').value).toBe(profile.id)
  expect(field(container, 'Book title').value).toBe('Draft book')
  expect(field(container, 'Chapter 1 title').value).toBe('Keep heading')
  expect(field(container, 'Chapter 1 text').value).toBe('Draft chapter text')
})

it('owns narrator refresh while creation opens and aborts it at teardown', async () => {
  const container = await mount()
  const request = deferred<SpeechVoiceProfile[]>()
  vi.mocked(profilesApi.listSpeechVoiceProfiles).mockReturnValue(request.promise)
  await draft(container); submit(container); await settle()
  expect(api.createAudiobook).not.toHaveBeenCalled()
  expect(button(container, 'Create audiobook').disabled).toBe(true)
  app?.unmount(); app = undefined
  expect(vi.mocked(profilesApi.listSpeechVoiceProfiles).mock.calls.at(-1)?.[0]?.aborted).toBe(true)
  request.resolve([profile]); await settle()
})

it('keeps an unavailable narrator draft explicit rather than silently switching the voice on reload', async () => {
  const otherProfile = { ...profile, id: 'd'.repeat(32), name: 'Other narrator' }
  vi.mocked(profilesApi.listSpeechVoiceProfiles).mockResolvedValue([profile, otherProfile])
  const container = await mount(); await draft(container); await change(container, 'Narrator', otherProfile.id)
  vi.mocked(profilesApi.listSpeechVoiceProfiles).mockResolvedValue([profile])
  await click(container, 'Reload library')
  expect(field(container, 'Narrator').value).toBe(otherProfile.id)
  expect(button(container, 'Create audiobook').disabled).toBe(true)
  expect(container.textContent).toContain('The narrator profile is unavailable')
})

it('waits for an owned library reload before submitting a book mutation', async () => {
  const container = await mount(); await draft(container)
  const request = deferred<AudiobookBook[]>()
  vi.mocked(api.listAudiobooks).mockReturnValue(request.promise)
  await click(container, 'Reload library'); submit(container); await settle()
  expect(api.createAudiobook).not.toHaveBeenCalled()
  expect(button(container, 'Create audiobook').disabled).toBe(true)
  request.resolve([book()]); await settle()
  expect(button(container, 'Create audiobook').disabled).toBe(false)
})

it('waits for a library reload before retrying to prevent stale list results replacing retry status', async () => {
  const failed = book(undefined, undefined, 'failed')
  vi.mocked(api.listAudiobooks).mockResolvedValue([failed])
  const container = await mount()
  const request = deferred<AudiobookBook[]>()
  vi.mocked(api.listAudiobooks).mockReturnValue(request.promise)
  await click(container, 'Reload library')
  expect(button(container, 'Retry failed chapters').disabled).toBe(true)
  button(container, 'Retry failed chapters').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
  expect(api.retryAudiobook).not.toHaveBeenCalled()
  request.resolve([failed]); await settle()
})
