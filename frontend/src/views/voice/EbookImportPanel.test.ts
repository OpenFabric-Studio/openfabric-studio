// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import EbookImportPanel from './EbookImportPanel.vue'
import * as api from '../../api/audiobooks'
import { ApiError } from '../../api/http'
import type { EbookDraft } from '../../api/contracts'
import en from '../../locales/en'

vi.mock('../../api/audiobooks', async original => ({ ...await original<typeof import('../../api/audiobooks')>(), listEbookDrafts: vi.fn(), getEbookDraft: vi.fn(), importEbook: vi.fn(), deleteEbookDraft: vi.fn() }))
const draft: EbookDraft = { id: 'f'.repeat(32), title: 'Story', chapters: [{ title: 'One', text: 'Chapter text', included: true }], source_filename: 'story.mobi', source_sha256: 'a'.repeat(64), warnings: [], revision: 1, created_at: 'today', updated_at: 'today' }
const summary = { id: draft.id, title: draft.title, source_filename: draft.source_filename, chapter_count: 1, revision: 1, created_at: 'today', updated_at: 'today' }
let app: App | undefined
const useDraft = vi.fn(), deletedDraft = vi.fn()
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.listEbookDrafts).mockResolvedValue([summary]) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount() {
  app = createApp(EbookImportPanel, { disabled: false, onUseDraft: useDraft, onDeletedDraft: deletedDraft }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle(); return node
}
function button(node: HTMLElement, text: string) { const result = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === text); if (!result) throw new Error(`Missing ${text}`); return result }
async function upload(node: HTMLElement, filename = 'story.mobi') {
  const input = node.querySelector('input'); if (!(input instanceof HTMLInputElement)) throw new Error('Missing upload')
  Object.defineProperty(input, 'files', { value: [new File(['fixture'], filename)], configurable: true })
  input.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
async function choose(node: HTMLElement) { const select = node.querySelector('select'); if (!select) throw new Error('Missing saved drafts'); select.value = draft.id; select.dispatchEvent(new Event('change', { bubbles: true })); await settle() }
function deferred<T>() { let resolve: (value: T) => void = () => { throw new Error('Not ready') }; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }

it('requires explicit chapter replacement after a successful upload', async () => {
  vi.mocked(api.importEbook).mockResolvedValue(draft)
  const node = await mount(); await upload(node)
  expect(useDraft).not.toHaveBeenCalled(); button(node, 'Use imported chapters').click(); await settle()
  expect(useDraft).toHaveBeenCalledWith(draft)
})

it.each(['docx', 'srt', 'vtt'])('accepts %s documents while retaining explicit review before use', async format => {
  vi.mocked(api.importEbook).mockResolvedValue(draft)
  const node = await mount(); await upload(node, `story.${format}`)
  expect(api.importEbook).toHaveBeenCalled()
  expect(useDraft).not.toHaveBeenCalled()
  expect(node.querySelector('input[type="file"]')?.getAttribute('accept')).toContain(`.${format}`)
})

it('shows source review warnings and only a bounded preview of preserved subtitle cues', async () => {
  const subtitle: EbookDraft = { ...draft, subtitle_import: true, cast_review_required: true,
    warnings: [{ code: 'subtitle_overlap', message: 'Source cues overlap; review dialogue order.' }],
    chapters: [{ title: 'Scene', text: 'Alice: Hello.', included: true,
      source_cues: Array.from({ length: 51 }, (_, index) => ({ cue_id: `source-${index}`, order: index,
        speaker: 'Alice', start_ms: 1000 + index * 1000, end_ms: 1500 + index * 1000, text: `Line ${index}` })) }] }
  vi.mocked(api.importEbook).mockResolvedValue(subtitle)
  const node = await mount(); await upload(node, 'scene.vtt')
  expect(node.textContent).toContain('Source cues overlap; review dialogue order.')
  expect(node.querySelectorAll('tbody tr')).toHaveLength(50)
  expect(node.querySelector('tbody')?.textContent).toContain('source-0')
  expect(node.querySelector('tbody')?.textContent).not.toContain('source-50')
  expect(useDraft).not.toHaveBeenCalled()
  button(node, 'Use imported chapters').click(); await settle()
  expect(useDraft).toHaveBeenCalledWith(subtitle)
})

it('shows safe converter guidance and leaves editor data untouched on failure', async () => {
  vi.mocked(api.importEbook).mockRejectedValue(new ApiError('ebook_converter_missing', 503))
  const node = await mount(); await upload(node)
  expect(node.querySelector('a')?.getAttribute('href')).toBe('/settings#module-ebooks')
  expect(node.textContent).toContain('Install Calibre'); expect(useDraft).not.toHaveBeenCalled()
  vi.mocked(api.importEbook).mockRejectedValue(new Error('/private/path traceback')); await upload(node)
  expect(node.textContent).not.toContain('/private/path'); expect(useDraft).not.toHaveBeenCalled()
})

it('ignores a late conversion result after cancellation', async () => {
  const request = deferred<EbookDraft>(); vi.mocked(api.importEbook).mockReturnValue(request.promise)
  const node = await mount(); await upload(node); button(node, 'Cancel import').click(); await settle()
  expect(vi.mocked(api.importEbook).mock.calls[0]?.[1]?.aborted).toBe(true)
  request.resolve(draft); await settle(); expect(node.textContent).not.toContain('Use imported chapters'); expect(useDraft).not.toHaveBeenCalled()
})

it('aborts an opening request on teardown and ignores the returned draft', async () => {
  const request = deferred<EbookDraft>(); vi.mocked(api.getEbookDraft).mockReturnValue(request.promise)
  const node = await mount(); await choose(node); app?.unmount(); app = undefined
  expect(vi.mocked(api.getEbookDraft).mock.calls[0]?.[1]?.aborted).toBe(true)
  request.resolve(draft); await settle(); expect(useDraft).not.toHaveBeenCalled()
})

it('requires deletion confirmation and reports deletion only after the backend succeeds', async () => {
  vi.mocked(api.getEbookDraft).mockResolvedValue(draft); const request = deferred<void>(); vi.mocked(api.deleteEbookDraft).mockReturnValue(request.promise)
  const node = await mount(); await choose(node); button(node, 'Delete saved draft').click(); await settle()
  expect(api.deleteEbookDraft).not.toHaveBeenCalled(); button(node, 'Delete source and saved draft').click(); await settle()
  expect(node.textContent).toContain('Deleting saved import'); expect(node.textContent).not.toContain('Converting')
  expect(deletedDraft).not.toHaveBeenCalled(); request.resolve(); await settle(); expect(deletedDraft).toHaveBeenCalledWith(draft.id)
})
