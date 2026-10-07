// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import { readingMediaEn } from '../locales/readingMedia'
import type { ReadAlongExport, RetainedAudioInfo, RetainedAudioSource } from '../api/contracts'
import * as api from '../api/readingMedia'
import { videoProjectFixture } from '../views/video/videoFixtures'
import ChapterReadAlong from './ChapterReadAlong.vue'
import RetainedAudioVideo from './RetainedAudioVideo.vue'
import { claimPlayback, releasePlaybackIfCurrent } from '../composables/audioPlayback'
vi.mock('../api/readingMedia', () => ({ listReadAlong: vi.fn(), createReadAlong: vi.fn(),
  getReadAlong: vi.fn(), cancelReadAlong: vi.fn(), resumeReadAlong: vi.fn(), retainedAudioInfo: vi.fn(), retainedAudioVideo: vi.fn() }))
let app: App | undefined
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en: { readingMedia: readingMediaEn } } })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllMocks(); vi.useRealTimers() })
async function flush() { for (let index = 0; index < 8; index++) await nextTick() }
function click(selector: string) {
  const button = document.querySelector(selector)
  if (!(button instanceof HTMLButtonElement)) throw new Error(`Missing button ${selector}`)
  button.click()
}
function input(selector: string, value: string) {
  const field = document.querySelector(selector)
  if (!(field instanceof HTMLInputElement)) throw new Error(`Missing input ${selector}`)
  field.value = value; field.dispatchEvent(new Event('input'))
}
const source: RetainedAudioSource = { kind: 'speech_trial', source_id: 'a'.repeat(32), chapter_index: null, revision: null }
const info: RetainedAudioInfo = { source, duration_ms: 30000, source_sha256: 'a'.repeat(64), content_origin: 'generated' }
function exportFixture(): ReadAlongExport {
  return { id: 'b'.repeat(32), book_id: 'a'.repeat(32), chapter_index: 0, source_revision: 1,
    source_sha256: 'a'.repeat(64), status: 'done', detail: '', aspect: 'portrait', preview_seconds: 25,
    duration_ms: 20000, timing: 'passage', text_basis: 'spoken_fallback', created_at: 'today', updated_at: 'today',
    video_url: '/private/not-trusted', srt_url: null, vtt_url: null, manifest_url: null }
}
it('requires explicit bounded selection for long recordings and does not auto-open a draft', async () => {
  vi.mocked(api.retainedAudioInfo).mockResolvedValue(info)
  vi.mocked(api.retainedAudioVideo).mockResolvedValue(videoProjectFixture(undefined, null))
  const open = vi.fn()
  app = createApp({ render: () => h(RetainedAudioVideo, { source, 'onOpen-video': open }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')))
  click('[data-inspect]'); await flush()
  click('[data-create]'); await flush()
  expect(api.retainedAudioVideo).not.toHaveBeenCalled()
  expect(document.body.textContent).toContain('Choose a clip between 0.2 and 15 seconds')
  input('[data-start]', '10'); input('[data-end]', '20'); await flush()
  click('[data-create]'); await flush()
  expect(api.retainedAudioVideo).toHaveBeenCalledWith(expect.objectContaining({ clip_start_ms: 10000,
    clip_end_ms: 20000, source_sha256: info.source_sha256 }), expect.any(AbortSignal))
  expect(open).not.toHaveBeenCalled()
  click('[data-open]'); expect(open).toHaveBeenCalledOnce()
})
it('drops a late source lookup after the selected recording changes', async () => {
  let complete: ((value: RetainedAudioInfo) => void) | undefined
  vi.mocked(api.retainedAudioInfo).mockImplementation(() => new Promise(resolve => { complete = resolve }))
  const selected = ref(source)
  app = createApp({ render: () => h(RetainedAudioVideo, { source: selected.value }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')))
  click('[data-inspect]'); await flush()
  selected.value = { ...source, source_id: 'b'.repeat(32) }; await flush()
  if (!complete) throw new Error('Source request did not start')
  complete(info); await flush()
  expect(document.querySelector('[data-create]')).toBeNull()
})
it('restores saved exports with honest fallback and builds constrained artifact links', async () => {
  vi.mocked(api.listReadAlong).mockResolvedValue([exportFixture()])
  app = createApp({ render: () => h(ChapterReadAlong, { bookId: 'a'.repeat(32), chapterIndex: 0, revision: 2 }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await flush()
  expect(document.body.textContent).toContain('earlier saved chapter revision')
  expect(document.body.textContent).toContain('no verified original-spelling mapping')
  expect(document.querySelector('a')?.getAttribute('href')).toBe(`/api/reading-media/exports/${'b'.repeat(32)}/movie.mp4`)
  expect(document.body.textContent).toContain('not individual words')
})
it('does not restore an old chapter export response after navigating away', async () => {
  let complete: ((value: ReadAlongExport[]) => void) | undefined
  vi.mocked(api.listReadAlong).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
    .mockResolvedValueOnce([])
  const chapter = ref(0)
  app = createApp({ render: () => h(ChapterReadAlong, { bookId: 'a'.repeat(32), chapterIndex: chapter.value, revision: 1 }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await flush()
  chapter.value = 1; await flush()
  if (!complete) throw new Error('Chapter request did not start')
  complete([exportFixture()]); await flush()
  expect(document.querySelector('article')).toBeNull()
})
it('stops polling when hidden without abandoning the saved backend export', async () => {
  vi.useFakeTimers()
  const running = { ...exportFixture(), status: 'running' as const }
  let complete: ((value: ReadAlongExport) => void) | undefined
  vi.mocked(api.listReadAlong).mockResolvedValue([running])
  vi.mocked(api.getReadAlong).mockImplementation(() => new Promise(resolve => { complete = resolve }))
  const active = ref(true)
  app = createApp({ render: () => h(ChapterReadAlong, { bookId: 'a'.repeat(32), chapterIndex: 0, revision: 1, active: active.value }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await flush()
  await vi.advanceTimersByTimeAsync(1000)
  expect(api.getReadAlong).toHaveBeenCalledOnce()
  active.value = false; await flush()
  if (!complete) throw new Error('Poll did not start')
  complete(exportFixture()); await flush()
  await vi.advanceTimersByTimeAsync(5000)
  expect(api.getReadAlong).toHaveBeenCalledOnce()
  expect(api.cancelReadAlong).not.toHaveBeenCalled()
  expect(document.querySelector('article')).toBeNull()
})
it('keeps cancellation terminal when an older aborted poll resolves late', async () => {
  vi.useFakeTimers()
  const running: ReadAlongExport = { ...exportFixture(), status: 'running' }
  let complete: ((value: ReadAlongExport) => void) | undefined
  vi.mocked(api.listReadAlong).mockResolvedValue([running])
  vi.mocked(api.getReadAlong).mockImplementation(() => new Promise(resolve => { complete = resolve }))
  vi.mocked(api.cancelReadAlong).mockResolvedValue({ ...running, status: 'cancelled' })
  app = createApp({ render: () => h(ChapterReadAlong, { bookId: 'a'.repeat(32), chapterIndex: 0, revision: 1 }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await flush()
  await vi.advanceTimersByTimeAsync(1000)
  click('article button'); await flush()
  expect(document.querySelector('article')?.textContent).toContain('Cancelled')
  if (!complete) throw new Error('Poll did not start')
  complete(running); await flush()
  await vi.advanceTimersByTimeAsync(5000)
  expect(document.querySelector('article')?.textContent).toContain('Cancelled')
  expect(api.getReadAlong).toHaveBeenCalledOnce()
})
it('plays a saved preview with shared controls and stops it when the view hides', async () => {
  vi.mocked(api.listReadAlong).mockResolvedValue([exportFixture()])
  const active = ref(true)
  app = createApp({ render: () => h(ChapterReadAlong, { bookId: 'a'.repeat(32), chapterIndex: 0, revision: 1, active: active.value }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await flush()
  const details = document.querySelector('details')
  if (!(details instanceof HTMLDetailsElement)) throw new Error('Missing preview section')
  details.open = true; details.dispatchEvent(new Event('toggle')); await flush()
  const video = document.querySelector('video')
  if (!(video instanceof HTMLVideoElement)) throw new Error('Missing saved preview player')
  expect(video.controls).toBe(true)
  expect(video.autoplay).toBe(false)
  expect(video.getAttribute('src')).toBe(`/api/reading-media/exports/${'b'.repeat(32)}/movie.mp4`)
  const pause = vi.spyOn(video, 'pause')
  video.dispatchEvent(new Event('play'))
  active.value = false; await flush()
  expect(pause).toHaveBeenCalled()
  const other = document.createElement('audio')
  pause.mockClear(); claimPlayback(other)
  expect(pause).not.toHaveBeenCalled()
  releasePlaybackIfCurrent(other)
})
