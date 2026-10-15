// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import * as tracks from '../../api/tracks'
import LibraryPicker from './LibraryPicker.vue'
vi.mock('../../api/tracks', () => ({ listTracks: vi.fn(), uploadTrack: vi.fn() }))
let app: App | undefined
const track = { id: 42, short_id: 42, model: 'upload', title: 'Piano', created_at: 'now', lyrics: '', seed: null, duration_ms: 30000, wall_ms: null, params: {}, filename: 'piano.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: { vocals: '/vocals.wav' }, midi: null } as const
beforeEach(() => { vi.resetAllMocks(); vi.mocked(tracks.listTracks).mockResolvedValue([track]) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let index = 0; index < 10; index++) await nextTick() }
async function mount(trackOnly = false, selectedTrackIds: number[] = []) { const picks: unknown[] = []; app = createApp(LibraryPicker, { trackOnly, selectedTrackIds, onPick: (value: unknown) => picks.push(value) }).use(i18n); const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle(); return picks }
function button(label: string) { const found = [...document.querySelectorAll('button')].find(element => element.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
it('returns a track ID for full mix selection and keeps legacy stem selection', async () => {
  const picks = await mount()
  button(i18n.global.t('library.fullMix')).click()
  expect(picks[0]).toEqual({ sourceUrl: track.audio_url, sourceLabel: track.title, trackId: 42 })
  expect(button('Vocals')).toBeDefined()
})
it('offers only whole tracks for the training picker', async () => {
  await mount(true)
  expect([...document.querySelectorAll('button')].some(element => element.textContent?.trim() === 'Vocals')).toBe(false)
})
it('marks existing training selections and prevents adding them twice', async () => {
  const picks = await mount(true, [42])
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Already added')
  const selected = button('Already added')
  expect(selected.disabled).toBe(true)
  selected.click()
  expect(picks).toEqual([])
})
