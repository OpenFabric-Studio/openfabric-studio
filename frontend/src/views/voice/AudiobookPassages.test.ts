// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import AudiobookPassages from './AudiobookPassages.vue'
import * as api from '../../api/audiobookWorkflow'
import type { AudiobookPassagesResponse, AudiobookRepair } from '../../api/contracts'
import en from '../../locales/en'
import * as timingApi from '../../api/narrationTiming'
vi.mock('../../api/narrationTiming', async original => ({ ...await original<typeof import('../../api/narrationTiming')>(), durationGuidance: vi.fn() }))
vi.mock('../../api/audiobookWorkflow', async original => ({ ...await original<typeof import('../../api/audiobookWorkflow')>(), __v_isRef: false, listPassages: vi.fn(), listPassageRepairs: vi.fn(), createRepair: vi.fn(), getRepair: vi.fn(), acceptRepair: vi.fn(), cancelRepair: vi.fn() }))
vi.mock('./PassageAsrReview.vue', () => ({ default: { render: () => null } }))
vi.mock('./PauseAnalysisSettings.vue', () => ({ default: { render: () => null } }))
const book = 'a'.repeat(32), passage = 'b'.repeat(32)
const data: AudiobookPassagesResponse = { book_id: book, chapter_index: 0, revision: 3, passages: [{ id: passage, section_index: 0, text: 'Accepted words', profile_id: 'c'.repeat(32), speaker: 'Alice', start_ms: 0, end_ms: 4000, status: 'done', render_identity: 'd'.repeat(64), language: 'en', audio_url: `/api/audiobooks/${book}/passages/${passage}/audio?revision=3` }] }
const candidate: AudiobookRepair = { id: 'e'.repeat(32), book_id: book, chapter_index: 0, passage_id: passage, revision: 3, status: 'ready', text: 'Accepted words', audio_url: `/api/audiobooks/repairs/${'e'.repeat(32)}/audio`, created_at: 'now', updated_at: 'now' }
let app: App | undefined
beforeEach(() => { vi.mocked(timingApi.durationGuidance).mockResolvedValue({ state: 'unavailable', reason: 'model_unverified', target_seconds: 30 }); vi.mocked(api.listPassages).mockResolvedValue(data); vi.mocked(api.listPassageRepairs).mockResolvedValue([]); vi.mocked(api.createRepair).mockResolvedValue(candidate) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.resetAllMocks() })
async function settle() { for (let i = 0; i < 10; i++) await nextTick() }
async function mount(bookId = ref(book)) {
  app = createApp({ render: () => h(AudiobookPassages, { bookId: bookId.value, chapterIndex: 0, active: true, playbackSeconds: 1 }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle(); return container
}
async function click(container: HTMLElement, text: string) {
  const target = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!target) throw new Error(`Missing button ${text}`)
  target.click(); await settle()
}
it('keeps accepted audio while auditioning a fresh take and only replaces after explicit acceptance', async () => {
  const updated = { ...data, revision: 4, passages: data.passages.map(item => ({ ...item, audio_url: `/api/audiobooks/${book}/passages/${passage}/audio?revision=4` })) }
  vi.mocked(api.acceptRepair).mockResolvedValue(updated)
  const container = await mount(); await click(container, 'Review passages'); await click(container, 'Find passage')
  await click(container, 'Generate a fresh take')
  expect(api.createRepair).toHaveBeenCalledWith(book, 0, passage, { revision: 3, text: 'Accepted words' }, expect.any(AbortSignal))
  expect(api.acceptRepair).not.toHaveBeenCalled()
  expect(container.querySelectorAll('audio')).toHaveLength(2)
  expect(container.textContent).toContain('Accepted passage'); expect(container.textContent).toContain('New take')
  await click(container, 'Accept this take')
  expect(api.acceptRepair).toHaveBeenCalledWith(candidate.id, 3, expect.any(AbortSignal))
  expect(container.textContent).toContain('Passage replaced')
  expect(container.querySelector('audio')?.getAttribute('src')).toContain('?revision=4')
})
it('ignores a late candidate after switching books', async () => {
  let resolve: (value: AudiobookRepair) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.createRepair).mockReturnValue(new Promise(release => { resolve = release }))
  const selected = ref(book), container = await mount(selected)
  await click(container, 'Review passages'); await click(container, 'Generate a fresh take')
  selected.value = 'f'.repeat(32); await settle(); resolve(candidate); await settle()
  expect(container.textContent).not.toContain('New take')
  expect(container.querySelector('audio')).toBeNull()
})

it('persists rejection of a ready take so it cannot return after reloading passages', async () => {
  let stored = { ...candidate, text: 'Previously generated words' }
  vi.mocked(api.listPassageRepairs).mockImplementation(async () => [stored])
  vi.mocked(api.cancelRepair).mockImplementation(async () => {
    stored = { ...stored, status: 'cancelled' }
    return stored
  })
  const container = await mount(); await click(container, 'Review passages')
  expect(container.textContent).toContain('Previously generated words')
  expect(api.acceptRepair).not.toHaveBeenCalled()
  await click(container, 'Keep accepted audio')
  expect(api.cancelRepair).toHaveBeenCalledWith(candidate.id, expect.any(AbortSignal))
  expect(container.textContent).not.toContain('Previously generated words')
  await click(container, 'Reload passages')
  expect(container.textContent).not.toContain('New take')
  expect(container.querySelectorAll('audio')).toHaveLength(1)
})
