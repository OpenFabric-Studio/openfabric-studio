// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import CastCheckPanel from './CastCheckPanel.vue'
import * as api from '../../api/audiobookWorkflow'
import type { AudiobookCastCheck, AudiobookJob, CreateAudiobookRequest } from '../../api/contracts'
import en from '../../locales/en'

vi.mock('../../api/audiobookWorkflow', async original => ({ ...await original<typeof import('../../api/audiobookWorkflow')>(), checkDraftCast: vi.fn(), checkBookCast: vi.fn() }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.resetAllMocks() })
async function settle() { for (let index = 0; index < 8; index++) await nextTick() }
const draft: CreateAudiobookRequest = { title: 'Scene', profile_id: 'a'.repeat(32), chapters: [{ text: 'Alice: Hello.' }], cast: [{ name: 'Alice', profile_id: 'b'.repeat(32) }] }
const report: AudiobookCastCheck = { book_id: null, chapter_index: 0, revision: null,
  turns: [{ speaker: 'Alice', profile_id: 'b'.repeat(32), profile_name: 'Alice voice', text: 'Hello.' }],
  warnings: [{ code: 'shared_narrator', speaker: 'Twin', profile_id: 'a'.repeat(32) }] }
function mount(render: () => ReturnType<typeof h>) {
  app = createApp({ render }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); return node
}
function checkButton(node: HTMLElement) {
  const found = [...node.querySelectorAll('button')].find(button => button.textContent === 'Check cast')
  if (!found) throw new Error('Missing cast check button')
  return found
}
it('shows resolved voices and advisory warnings without requesting synthesis', async () => {
  vi.mocked(api.checkDraftCast).mockResolvedValue(report)
  const node = mount(() => h(CastCheckPanel, { draft }))
  checkButton(node).click(); await settle()
  expect(api.checkDraftCast).toHaveBeenCalledWith({ ...draft, chapter_index: 0 }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Alice voice'); expect(node.textContent).toContain('Hello.')
  expect(node.textContent).toContain('“Twin” uses the same voice as the narrator.')
  expect(node.querySelector('audio')).toBeNull()
})
it.each(['edit', 'hide', 'unmount'])('discards an outstanding result after %s', async action => {
  let release: (value: AudiobookCastCheck) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.checkDraftCast).mockReturnValue(new Promise(resolve => { release = resolve }))
  const value = ref(draft), active = ref(true), node = mount(() => h(CastCheckPanel, { draft: value.value, active: active.value }))
  checkButton(node).click(); await settle()
  const signal = vi.mocked(api.checkDraftCast).mock.calls[0]?.[1]
  if (action === 'edit') value.value = { ...draft, chapters: [{ text: 'Changed.' }] }
  else if (action === 'hide') active.value = false
  else { app?.unmount(); app = undefined }
  await settle(); release(report); await settle()
  expect(signal?.aborted).toBe(true); expect(node.textContent).not.toContain('Alice voice')
})
it('uses the selected saved chapter revision and rejects mismatched responses', async () => {
  const bookId = 'c'.repeat(32)
  const jobs: AudiobookJob[] = [1, 8].map((revision, index) => ({ id: String(index + 1).repeat(32), book_id: bookId, chapter_index: index, chapter_title: `Chapter ${index + 1}`, status: 'done', revision, created_at: 'now', updated_at: 'now' }))
  vi.mocked(api.checkBookCast).mockResolvedValue({ ...report, book_id: bookId, chapter_index: 1, revision: 9 })
  const node = mount(() => h(CastCheckPanel, { bookId, jobs }))
  const selector = node.querySelector('select'); if (!selector) throw new Error('Missing chapter selector')
  selector.value = '1'; selector.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  checkButton(node).click(); await settle()
  expect(api.checkBookCast).toHaveBeenCalledWith(bookId, { chapter_index: 1, revision: 8 }, expect.any(AbortSignal))
  expect(node.querySelector('[role=alert]')?.textContent).toContain('Could not check this cast')
  expect(node.textContent).not.toContain('Alice voice')
})
it.each(['saved', 'draft'])('selects an available chapter when %s chapters shrink', async source => {
  const bookId = ref('c'.repeat(32)), count = ref(2)
  const node = mount(() => source === 'saved'
    ? h(CastCheckPanel, { bookId: bookId.value, jobs: Array.from({ length: count.value }, (_, index): AudiobookJob => ({ id: String(index + 1).repeat(32), book_id: bookId.value, chapter_index: index, chapter_title: `Chapter ${index + 1}`, status: 'done', revision: 1, created_at: 'now', updated_at: 'now' })) })
    : h(CastCheckPanel, { draft: { ...draft, chapters: Array.from({ length: count.value }, () => ({ text: 'Hello.' })) } }))
  const selector = node.querySelector('select'); if (!selector) throw new Error('Missing chapter selector')
  selector.value = '1'; selector.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  bookId.value = 'd'.repeat(32); count.value = 1; await settle()
  expect(node.querySelector('select')).toBeNull()
  expect(checkButton(node).disabled).toBe(false)
  checkButton(node).click(); await settle()
  if (source === 'saved') expect(api.checkBookCast).toHaveBeenCalledWith(bookId.value, { chapter_index: 0, revision: 1 }, expect.any(AbortSignal))
  else expect(api.checkDraftCast).toHaveBeenCalledWith(expect.objectContaining({ chapter_index: 0 }), expect.any(AbortSignal))
})
