// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import ReferencePreparationPanel from './ReferencePreparationPanel.vue'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/references'
import * as tracks from '../../api/tracks'
import type { ReferenceImport } from '../../api/contracts'
import * as termsApi from '../../api/musicTerms'
import { ApiError } from '../../api/http'
import type { MusicTermsSnapshot } from '../../api/generated'
vi.mock('../../api/references', () => ({ capabilities: vi.fn(), list: vi.fn(), probe: vi.fn(), submit: vi.fn(), prepare: vi.fn(), cancel: vi.fn(), retry: vi.fn(), remove: vi.fn(), transformAbc: vi.fn(), transformLyrics: vi.fn() }))
vi.mock('../../api/tracks', () => ({ uploadTrack: vi.fn(), listTracks: vi.fn() }))
vi.mock('./TrackAudioVersions.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
vi.mock('../../api/musicTerms', () => ({ getMusicTerms: vi.fn(), acceptMusicTerms: vi.fn(), updateMusicTermsSettings: vi.fn() }))
function terms(allowed = false): MusicTermsSnapshot {
  return { engine: 'yue2', operation: 'transcription', revision: 3, noncommercial_enabled: allowed, allowed, requirements: [{ component_id: 'sheet-sage', name: 'SheetSage weights', scope: 'model', restriction: 'noncommercial', source_url: 'https://example.org/terms', terms_digest: 'a'.repeat(64), acknowledged: allowed }] }
}
const done: ReferenceImport = { id: 'a'.repeat(32), status: 'partial', request: { track_id: 7, kind: 'track', melody: true }, track_id: 7, audio_url: '/source.wav', stages: [{ name: 'download', status: 'done' }, { name: 'lyrics', status: 'done' }, { name: 'separation', status: 'skipped' }, { name: 'melody', status: 'failed', error_code: 'tool_failed' }], lyrics: { lyrics: '[Verse]\nreview me', lines: [] }, abc: null, created_at: '2026-10-01', updated_at: '2026-10-01' }
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); setLocale('en'); vi.useFakeTimers(); vi.mocked(api.capabilities).mockResolvedValue({ source_import: { available: false, setup_hint: 'Install yt-dlp' }, subtitles: { available: false }, whisper: { available: false, setup_hint: 'Install Whisper' }, separation: [], melody: { available: false, setup_hint: 'Install SheetSage' } }); vi.mocked(api.list).mockResolvedValue([done]); vi.mocked(tracks.listTracks).mockResolvedValue([]); vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms(true)) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 10; index++) await nextTick() }
async function mount(apply = vi.fn()) { const container = document.body.appendChild(document.createElement('div')); app = createApp(ReferencePreparationPanel, { engine: 'yue2', onApplyLyrics: apply }).use(i18n); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
it('restores durable partial results but applies reviewed text only after explicit action', async () => {
  const apply = vi.fn(); const container = await mount(apply)
  expect(apply).not.toHaveBeenCalled(); expect(container.textContent).toContain('Install Whisper')
  button(container, 'Review results').click(); await settle()
  const lyrics = container.querySelector<HTMLTextAreaElement>('[aria-label="Reviewed lyrics"]'); if (!lyrics) throw new Error('Missing lyrics review')
  lyrics.value = 'my reviewed lyrics'; lyrics.dispatchEvent(new Event('input')); await settle(); button(container, 'Apply lyrics').click()
  expect(apply).toHaveBeenCalledWith('my reviewed lyrics', done.id); expect(container.textContent).toContain('The tool failed.')
})
it('cancels the backend job and ignores a late list response', async () => {
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms())
  const running = { ...done, status: 'running' as const }; vi.mocked(api.list).mockResolvedValue([running]); vi.mocked(api.cancel).mockResolvedValue({ ...done, status: 'cancelled' })
  const container = await mount(); button(container, 'Cancel').click(); await settle()
  expect(api.cancel).toHaveBeenCalledWith(done.id, expect.any(AbortSignal)); expect(container.textContent).toContain('Cancelled')
})
it('does not restart polling after a delayed submission returns following teardown', async () => {
  vi.mocked(api.list).mockResolvedValue([]); vi.mocked(api.capabilities).mockResolvedValue({ source_import: { available: true }, subtitles: { available: true }, whisper: { available: true }, separation: [], melody: { available: true } })
  vi.mocked(api.probe).mockResolvedValue({ canonical_url: 'https://www.youtube.com/watch?v=abcdefghijk', video_id: 'abcdefghijk', title: 'Source', duration_seconds: 10, subtitle_languages: [] })
  let resolve = (_row: ReferenceImport): void => { throw new Error('Uninitialized') }; vi.mocked(api.submit).mockReturnValue(new Promise(done => { resolve = done }))
  const container = await mount(); const input = container.querySelector<HTMLInputElement>('input[type="url"]'); if (!input) throw new Error('Missing URL')
  input.value = 'https://www.youtube.com/watch?v=abcdefghijk'; input.dispatchEvent(new Event('input')); await settle(); button(container, 'Inspect source').click(); await settle(); button(container, 'Prepare reference').click(); await settle(); app?.unmount(); app = undefined; resolve(done); await settle(); await vi.advanceTimersByTimeAsync(10000)
  expect(api.list).toHaveBeenCalledTimes(1)
})
it('keeps the review textarea mounted while the user clears it', async () => {
  const container = await mount(); button(container, 'Review results').click(); await settle()
  const lyrics = container.querySelector<HTMLTextAreaElement>('[aria-label="Reviewed lyrics"]'); if (!lyrics) throw new Error('Missing review')
  lyrics.value = ''; lyrics.dispatchEvent(new Event('input')); await settle()
  expect(container.querySelector('[aria-label="Reviewed lyrics"]')).toBe(lyrics)
  expect(button(container, 'Apply lyrics').disabled).toBe(true)
})

it('allows retrying failed lyrics when melody already completed and NC is disabled', async () => {
  const partial: ReferenceImport = { ...done, abc: 'X:1\nK:C\nC D E', stages: done.stages.map(stage => ({ ...stage,
    status: stage.name === 'melody' ? 'done' : stage.name === 'lyrics' ? 'failed' : stage.status })) }
  vi.mocked(api.list).mockResolvedValue([partial]); vi.mocked(api.retry).mockResolvedValue(partial)
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms())
  const container = await mount()
  const melody = container.querySelector('input[type=checkbox]')
  if (!(melody instanceof HTMLInputElement)) throw new Error('Missing melody control')
  melody.click(); await settle()
  const retry = button(container, 'Retry')
  expect(retry.disabled).toBe(false)
  retry.click(); await settle()
  expect(api.retry).toHaveBeenCalledWith(partial.id, expect.any(AbortSignal))
})

async function inspectSource(container: HTMLElement) {
  const input = container.querySelector<HTMLInputElement>('input[type="url"]'); if (!input) throw new Error('Missing source URL')
  input.value = 'https://www.youtube.com/watch?v=abcdefghijk'; input.dispatchEvent(new Event('input')); await settle()
  button(container, 'Inspect source').click(); await settle()
}
function availableTools() {
  vi.mocked(api.capabilities).mockResolvedValue({ source_import: { available: true }, subtitles: { available: true }, whisper: { available: true }, separation: [], melody: { available: true } })
  vi.mocked(api.probe).mockResolvedValue({ canonical_url: 'https://www.youtube.com/watch?v=abcdefghijk', video_id: 'abcdefghijk', title: 'Source', duration_seconds: 10, subtitle_languages: ['en'] })
  vi.mocked(api.submit).mockResolvedValue(done)
}

it('gates melody preparation with the real terms card and preserves the source draft through acceptance', async () => {
  availableTools(); vi.mocked(api.list).mockResolvedValue([])
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms()); vi.mocked(termsApi.acceptMusicTerms).mockResolvedValue(terms(true))
  const container = await mount(); await inspectSource(container)
  expect(termsApi.getMusicTerms).not.toHaveBeenCalled()
  const melody = container.querySelector('input[type=checkbox]'); if (!(melody instanceof HTMLInputElement)) throw new Error('Missing melody choice')
  melody.click(); await settle(); expect(button(container, 'Prepare reference').disabled).toBe(true)
  const card = container.querySelector('section[aria-label="Publisher terms"]'); if (!card) throw new Error('Missing terms card')
  for (const input of card.querySelectorAll<HTMLInputElement>('input[type=checkbox]')) input.click()
  await settle(); button(container, 'Accept reviewed terms').click(); await settle()
  expect(button(container, 'Prepare reference').disabled).toBe(false)
  button(container, 'Prepare reference').click(); await settle()
  expect(api.submit).toHaveBeenCalledWith(expect.objectContaining({ url: 'https://www.youtube.com/watch?v=abcdefghijk', melody: true }), expect.any(AbortSignal))
  expect(termsApi.acceptMusicTerms).toHaveBeenCalledWith(expect.objectContaining({ engine: 'yue2', operation: 'transcription', allow_noncommercial: true }), expect.any(AbortSignal))
})

it.each(['whisper', 'subtitles'])('does not gate %s-only preparation on NC melody terms', async lyricsSource => {
  availableTools(); vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms())
  const container = await mount(); await inspectSource(container)
  const select = container.querySelector('option[value="whisper"]')?.parentElement; if (!(select instanceof HTMLSelectElement)) throw new Error('Missing lyrics selector')
  select.value = lyricsSource; select.dispatchEvent(new Event('change')); await settle()
  expect(button(container, 'Prepare reference').disabled).toBe(false)
  button(container, 'Prepare reference').click(); await settle()
  expect(api.submit).toHaveBeenCalledWith(expect.objectContaining({ melody: false, lyrics_source: lyricsSource }), expect.any(AbortSignal))
  expect(termsApi.acceptMusicTerms).not.toHaveBeenCalled()
})

it('gates retrying a saved melody job while leaving review and reload available', async () => {
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms()); vi.mocked(termsApi.acceptMusicTerms).mockResolvedValue(terms(true))
  vi.mocked(api.retry).mockResolvedValue({ ...done, status: 'queued' })
  const container = await mount()
  expect(button(container, 'Retry').disabled).toBe(true)
  expect(button(container, 'Review results').disabled).toBe(false)
  expect(button(container, 'Reload').disabled).toBe(false)
  const card = container.querySelector('section[aria-label="Publisher terms"]'); if (!card) throw new Error('Missing terms card')
  for (const input of card.querySelectorAll<HTMLInputElement>('input[type=checkbox]')) input.click()
  await settle(); button(container, 'Accept reviewed terms').click(); await settle(); button(container, 'Retry').click(); await settle()
  expect(api.retry).toHaveBeenCalledWith(done.id, expect.any(AbortSignal))
})

it.each(['music_terms_required', 'music_noncommercial_disabled'])('translates the reference server terms error %s', async code => {
  availableTools(); vi.mocked(api.list).mockResolvedValue([]); vi.mocked(api.submit).mockRejectedValue(new ApiError(code, 403))
  const container = await mount(); await inspectSource(container); button(container, 'Prepare reference').click(); await settle()
  expect(container.querySelector('[role=alert]')?.textContent).toContain('Review the publisher terms card for this operation')
})
