// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createPinia } from 'pinia'
import { i18n, setLocale } from '../../i18n'
import { parseSavedTrack, type SavedTrack } from '../../api/contracts'
import * as api from '../../api/tracks'
import FavoriteTrackButton from './FavoriteTrackButton.vue'

vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn(), setTrackFavorite: vi.fn() }))
function track(id: number, isFavorite = false): SavedTrack {
  return parseSavedTrack({ id, short_id: id, model: 'ace_step', title: `Song ${id}`, filename: `${id}.wav`, created_at: 'now', lyrics: '', seed: null, duration_ms: null, wall_ms: null, params: {}, audio_url: `/api/tracks/${id}/audio`, abc_url: null, stems: null, midi: null, is_favorite: isFavorite })
}
let app: App | undefined
beforeEach(() => { setLocale('en'); vi.clearAllMocks(); vi.mocked(api.listTracks).mockResolvedValue([track(1), track(2, true)]); vi.mocked(api.setTrackFavorite).mockImplementation(async (id, favorite) => track(id, favorite)) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function flush() { for (let i = 0; i < 10; i++) await nextTick() }
async function mount(id = ref(1)) {
  app = createApp({ render: () => h(FavoriteTrackButton, { trackId: id.value, label: `Song ${id.value}` }) }).use(createPinia()).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')))
  await flush()
  return id
}
function button(): HTMLButtonElement { const button = document.querySelector('button'); if (!button) throw new Error('Missing favorite button'); return button }
it('exposes an accessible per-track star and changes only after a confirmed save', async () => {
  await mount()
  expect(button().getAttribute('aria-label')).toBe('Add Song 1 to favorites')
  expect(button().getAttribute('aria-pressed')).toBe('false')
  button().click(); await flush()
  expect(api.setTrackFavorite).toHaveBeenCalledWith(1, true, expect.any(AbortSignal))
  expect(button().getAttribute('aria-pressed')).toBe('true')
  expect(button().getAttribute('aria-label')).toBe('Remove Song 1 from favorites')
})
it('disables repeated clicks while saving and shows a stable failure message', async () => {
  await mount()
  let reject: (error: Error) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.setTrackFavorite).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail }))
  button().click(); await flush()
  expect(button().disabled).toBe(true)
  button().click()
  expect(api.setTrackFavorite).toHaveBeenCalledTimes(1)
  reject(new Error('private server trace')); await flush()
  expect(button().getAttribute('aria-pressed')).toBe('false')
  expect(button().disabled).toBe(false)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('could not be saved')
  expect(document.body.textContent).not.toContain('private server trace')
})
it('aborts an old track mutation when the same component is assigned another track', async () => {
  const id = await mount()
  let release: (value: SavedTrack) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.setTrackFavorite).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  button().click(); await flush()
  const signal = vi.mocked(api.setTrackFavorite).mock.calls[0]?.[2]
  id.value = 2; await flush()
  expect(signal?.aborted).toBe(true)
  release(track(1, true)); await flush()
  expect(button().getAttribute('aria-label')).toBe('Remove Song 2 from favorites')
  expect(button().getAttribute('aria-pressed')).toBe('true')
})
it('aborts the mutation when the star is removed', async () => {
  await mount()
  let release: (value: SavedTrack) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.setTrackFavorite).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  button().click(); await flush()
  const signal = vi.mocked(api.setTrackFavorite).mock.calls[0]?.[2]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  release(track(1, true)); await flush()
})
