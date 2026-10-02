// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import ReferencePreparationPanel from './ReferencePreparationPanel.vue'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/references'
import * as tracks from '../../api/tracks'
import type { ReferenceImport } from '../../api/contracts'
vi.mock('../../api/references', () => ({ capabilities: vi.fn(), list: vi.fn(), probe: vi.fn(), submit: vi.fn(), prepare: vi.fn(), cancel: vi.fn(), retry: vi.fn(), remove: vi.fn(), transformAbc: vi.fn(), transformLyrics: vi.fn() }))
vi.mock('../../api/tracks', () => ({ uploadTrack: vi.fn(), listTracks: vi.fn() }))
vi.mock('./TrackAudioVersions.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
const done: ReferenceImport = { id: 'a'.repeat(32), status: 'partial', request: { track_id: 7, kind: 'track', melody: true }, track_id: 7, audio_url: '/source.wav', stages: [{ name: 'download', status: 'done' }, { name: 'lyrics', status: 'done' }, { name: 'separation', status: 'skipped' }, { name: 'melody', status: 'failed', error_code: 'tool_failed' }], lyrics: { lyrics: '[Verse]\nreview me', lines: [] }, abc: null, created_at: '2026-10-01', updated_at: '2026-10-01' }
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); setLocale('en'); vi.useFakeTimers(); vi.mocked(api.capabilities).mockResolvedValue({ source_import: { available: false, setup_hint: 'Install yt-dlp' }, subtitles: { available: false }, whisper: { available: false, setup_hint: 'Install Whisper' }, separation: [], melody: { available: false, setup_hint: 'Install SheetSage' } }); vi.mocked(api.list).mockResolvedValue([done]); vi.mocked(tracks.listTracks).mockResolvedValue([]) })
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
