// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import PassageAsrReview from './PassageAsrReview.vue'
import * as api from '../../api/audiobookReview'
import type { AsrReview } from '../../api/contracts'
import en from '../../locales/en'
vi.mock('../../api/audiobookReview', async original => ({ ...await original<typeof import('../../api/audiobookReview')>(), getAsrCapability: vi.fn(), listAsrReviews: vi.fn(), checkPassage: vi.fn(), getAsrReview: vi.fn() }))
let app: App | undefined
const book = 'a'.repeat(32), passage = 'b'.repeat(32), identity = 'c'.repeat(64)
const result: AsrReview = { id: 'd'.repeat(32), book_id: book, chapter_index: 0, passage_id: passage, revision: 2, render_identity: identity, audio_sha256: 'e'.repeat(64), state: 'completed', expected_text: 'Hello there', transcript: 'Hello', duration_ms: 1000, flags: [{ code: 'omission', message: 'Possible missing words. Listen to this passage.' }], created_at: 'now', updated_at: 'now' }
beforeEach(() => { vi.mocked(api.getAsrCapability).mockResolvedValue({ available: true }); vi.mocked(api.listAsrReviews).mockResolvedValue([]) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.resetAllMocks() })
async function settle() { for (let i = 0; i < 10; i++) await nextTick() }
async function mount(revision = ref(2), active = ref(true)) {
  app = createApp({ render: () => h(PassageAsrReview, { bookId: book, chapterIndex: 0, passageId: passage, revision: revision.value, renderIdentity: identity, active: active.value }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle(); return container
}
it('restores only reviews for the current accepted audio version', async () => {
  vi.mocked(api.listAsrReviews).mockResolvedValue([{ ...result, revision: 1, transcript: 'Old speech' }, result])
  const container = await mount()
  expect(container.textContent).toContain('Possible missing words')
  expect(container.textContent).not.toContain('Old speech')
})
it('keeps ASR opt-in and shows configured-engine unavailability', async () => {
  vi.mocked(api.getAsrCapability).mockResolvedValue({ available: false, reason: 'whisper_model_missing' })
  const container = await mount(), button = container.querySelector('button')
  expect(button?.disabled).toBe(true)
  expect(container.textContent).toContain('Local transcription is unavailable')
  expect(api.checkPassage).not.toHaveBeenCalled()
})
it('rejects late screening flags after a passage revision changes', async () => {
  let resolve: (value: AsrReview) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.checkPassage).mockReturnValue(new Promise(release => { resolve = release }))
  const revision = ref(2), container = await mount(revision)
  container.querySelector('button')?.click(); await settle()
  expect(api.checkPassage).toHaveBeenCalledWith(book, 0, passage, { revision: 2, render_identity: identity }, expect.any(AbortSignal))
  revision.value = 3; await settle(); resolve(result); await settle()
  expect(container.textContent).not.toContain('Possible missing words')
})
it('waits for saved jobs to restore on returning to the workspace before enabling another check', async () => {
  let resolve: (value: AsrReview[]) => void = () => { throw new Error('Not initialized') }
  const pending = new Promise<AsrReview[]>(release => { resolve = release })
  vi.mocked(api.listAsrReviews).mockResolvedValueOnce([]).mockReturnValueOnce(pending)
  const active = ref(true), container = await mount(ref(2), active)
  active.value = false; await settle(); active.value = true; await settle()
  expect(container.querySelector('button')?.disabled).toBe(true)
  container.querySelector('button')?.click(); await settle()
  expect(api.checkPassage).not.toHaveBeenCalled()
  const queued = { ...result, state: 'queued' as const }
  vi.mocked(api.getAsrReview).mockResolvedValue(queued)
  resolve([queued]); await settle()
  expect(container.textContent).toContain('Cancel check')
  expect(container.querySelector('button')?.disabled).toBe(true)
})
