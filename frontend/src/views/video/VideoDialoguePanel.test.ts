// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import type { AudiobookPassagesResponse, VideoDialogueCue, VideoProject } from '../../api/contracts'
import * as api from '../../api/videos'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import { videoProjectFixture } from './videoFixtures'
import VideoDialoguePanel from './VideoDialoguePanel.vue'
vi.mock('../../api/videos', async original => ({ ...await original<typeof import('../../api/videos')>(), dialoguePassages: vi.fn() }))
let app: App | undefined
const cue: VideoDialogueCue = { shot_id: 'b'.repeat(32), book_id: 'c'.repeat(32), chapter_index: 0, passage_id: 'd'.repeat(32), source_revision: 1, render_identity: 'e'.repeat(64), profile_id: 'f'.repeat(32), speaker: 'Actor', text: 'Old words', source_duration_ms: 1000, source_start_ms: 0, source_end_ms: 1000, start_sec: 0, end_sec: 1, audio_sha256: '0'.repeat(64) }
const passages: AudiobookPassagesResponse = { book_id: cue.book_id, chapter_index: 0, revision: 2, passages: [{ id: cue.passage_id, section_index: 0, text: 'Repaired words', profile_id: cue.profile_id, speaker: cue.speaker, start_ms: 0, end_ms: 1000, status: 'done', render_identity: '1'.repeat(64) }] }
function project(id = 'a'.repeat(32)): VideoProject { return { ...videoProjectFixture(id), dialogue_cues: [cue], overlays: [{ id: cue.shot_id, text: cue.text, start_sec: 0, end_sec: 1 }] } }
async function settle() { for (let index = 0; index < 6; index++) await nextTick() }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.dialoguePassages).mockResolvedValue(passages) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks() })
function button(node: HTMLElement): HTMLButtonElement { const element = node.querySelector('button'); if (!element) throw new Error('Missing refresh'); return element }
it('drops a late source response and resets refresh ownership when changing project', async () => {
  let resolve: (value: AudiobookPassagesResponse) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.dialoguePassages).mockReturnValueOnce(new Promise(release => { resolve = release }))
  const current = ref(project()), refresh = vi.fn()
  app = createApp({ render: () => h(VideoDialoguePanel, { project: current.value, readOnly: false, onRefresh: refresh }) }).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle()
  button(node).click(); await settle()
  const signal = vi.mocked(api.dialoguePassages).mock.calls[0]?.[2]
  current.value = project('9'.repeat(32)); await settle()
  expect(button(node).disabled).toBe(false)
  expect(signal?.aborted).toBe(true)
  resolve(passages); await settle(); expect(refresh).not.toHaveBeenCalled()
})
it('uses repaired whole-line captions instead of retaining old source words', async () => {
  const refresh = vi.fn()
  app = createApp({ render: () => h(VideoDialoguePanel, { project: project(), readOnly: false, onRefresh: refresh }) }).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle()
  button(node).click(); await settle()
  expect(refresh.mock.calls[0]?.[1]?.selections[0]?.caption).toBe('Repaired words')
})
it('stops and releases original cast playback on navigation and unmount', async () => {
  const current = ref(project())
  app = createApp({ render: () => h(VideoDialoguePanel, { project: current.value, readOnly: false }) }).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node)
  const audio = node.querySelector('audio'); if (!audio) throw new Error('Missing cast audio')
  const pause = vi.spyOn(audio, 'pause')
  audio.dispatchEvent(new Event('play')); current.value = project('9'.repeat(32)); await settle()
  expect(pause).toHaveBeenCalled()
  audio.dispatchEvent(new Event('play')); app.unmount(); app = undefined
  pause.mockClear(); const other = document.createElement('audio'); claimPlayback(other)
  expect(pause).not.toHaveBeenCalled(); releasePlaybackIfCurrent(other)
})

it('loads new cast PCM after refresh and undo instead of reusing the stable URL', async () => {
  const first = project()
  first.speech_clip = { id: '2'.repeat(32), name: 'Cast', bytes: 100, duration_sec: 2, sha256: '3'.repeat(64) }
  const current = ref(first)
  app = createApp({ render: () => h(VideoDialoguePanel, { project: current.value, readOnly: false }) }).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node)
  const original = node.querySelector('audio'); if (!original) throw new Error('Missing cast audio')
  const originalUrl = original.getAttribute('src')
  current.value = { ...first, speech_clip: { ...first.speech_clip, id: '4'.repeat(32) } }; await settle()
  expect(node.querySelector('audio')).not.toBe(original)
  expect(node.querySelector('audio')?.getAttribute('src')).not.toBe(originalUrl)
  current.value = first; await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe(originalUrl)
})

it('drops stale refresh intent when the same project is edited or undone during lookup', async () => {
  let resolve: (value: AudiobookPassagesResponse) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.dialoguePassages).mockReturnValueOnce(new Promise(release => { resolve = release }))
  const current = ref(project()), refresh = vi.fn()
  app = createApp({ render: () => h(VideoDialoguePanel, { project: current.value, readOnly: false, onRefresh: refresh }) }).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle()
  button(node).click(); await settle()
  const signal = vi.mocked(api.dialoguePassages).mock.calls[0]?.[2]
  current.value = { ...current.value, revision: 2 }; await settle()
  resolve(passages); await settle()
  expect(refresh).not.toHaveBeenCalled()
  expect(signal?.aborted).toBe(true)
})

it('lets a repaired line be trimmed explicitly with a reviewed clip caption', async () => {
  vi.mocked(api.dialoguePassages).mockResolvedValue({ ...passages, passages: passages.passages.map(item => ({ ...item, end_ms: 4000, text: 'A longer repaired line' })) })
  const refresh = vi.fn()
  app = createApp({ render: () => h(VideoDialoguePanel, { project: project(), readOnly: false, onRefresh: refresh }) }).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle()
  const start = node.querySelector<HTMLInputElement>('[data-cue-start]'), end = node.querySelector<HTMLInputElement>('[data-cue-end]'), caption = node.querySelector<HTMLInputElement>('input[maxlength="500"]')
  expect(start).not.toBeNull(); expect(end).not.toBeNull()
  if (!start || !end || !caption) throw new Error('Missing clip controls')
  start.value = '500'; end.value = '1500'; caption.value = 'Repaired selected words'
  for (const input of [start, end, caption]) input.dispatchEvent(new Event('input', { bubbles: true }))
  button(node).click(); await settle()
  expect(refresh.mock.calls[0]?.[1]?.selections[0]).toMatchObject({ clip_start_ms: 500, clip_end_ms: 1500, caption: 'Repaired selected words' })
})
