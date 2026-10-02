// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import ResultsFeed from '../../views/ace-step/ResultsFeed.vue'
import TrackFeed from '../../views/yue2/TrackFeed.vue'
import { useAceStepStore, type AceJob } from '../../stores/aceStep'
import { useYue2Store, type Yue2Job } from '../../stores/yue2'
import { useTrackView } from '../../composables/useTrackView'
import * as api from '../../api/tracks'
import { parseSavedTrack } from '../../api/contracts'
import { i18n, setLocale } from '../../i18n'

vi.mock('../../api/trackActivity', () => ({ listActiveAudioTrackIds: vi.fn().mockResolvedValue([]) }))
vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn(), setTrackFavorite: vi.fn() }))
vi.mock('./TrackAudioVersions.vue', () => ({ default: { props: ['trackId', 'fallbackAudioUrl'], template: '<div data-player :data-track="trackId" :data-fallback="fallbackAudioUrl"></div>' } }))
vi.mock('./BatchABPlayer.vue', () => ({ default: { template: '<div data-batch-player></div>' } }))
vi.mock('./WaveformPlayer.vue', () => ({ default: { template: '<div data-waveform-player></div>' } }))
vi.mock('./StemsPanel.vue', () => ({ default: { props: ['trackId'], template: '<div data-stems :data-track="trackId"></div>' } }))
vi.mock('./MidiPanel.vue', () => ({ default: { props: ['trackId'], template: '<div data-midi :data-track="trackId"></div>' } }))
function track(id: number, favorite: boolean) { return parseSavedTrack({ id, short_id: id + 100, model: 'ace_step', title: `Song ${id}`, filename: `${id}.wav`, created_at: '2026-10-01T10:00:00Z', lyrics: '', seed: null, duration_ms: null, wall_ms: null, params: {}, audio_url: `/api/tracks/${id}/audio`, abc_url: null, stems: null, midi: null, is_favorite: favorite }) }
const ace: AceJob = { id: 'batch', status: 'done', createdAt: new Date('2026-10-01T10:00:00Z').getTime(), title: 'Batch song', lyrics: '', audioFormat: 'wav', batchSize: 2, progress: 100, finalized: true, dbIds: [1, 2], shortIds: [101, 102], audioUrls: ['/first.wav', '/second.wav'] }
function yue(id: number, createdAt: string): Yue2Job { return { id: `saved_${id}`, dbId: id, shortId: id + 100, status: 'done', createdAt: new Date(createdAt).getTime(), title: `Song ${id}`, style: `Song ${id}`, lyrics: '', cot: 'off', precision: 'q8_0', seed: 1, finalized: true, audioUrl: `/${id}.wav` } }
let app: App | undefined
beforeEach(() => {
  setLocale('en'); vi.clearAllMocks(); useTrackView().setView('cards')
  vi.mocked(api.listTracks).mockResolvedValue([track(1, false), track(2, true), track(3, true)])
  vi.mocked(api.setTrackFavorite).mockImplementation(async (id, favorite) => track(id, favorite))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function flush() { for (let i = 0; i < 15; i++) await nextTick() }
async function mount(kind: 'ace' | 'yue') {
  const pinia = createPinia(); setActivePinia(pinia)
  if (kind === 'ace') { const store = useAceStepStore(); store.jobs = [{ ...ace }]; store.historyLoaded = true }
  else { const store = useYue2Store(); store.jobs = [yue(1, '2026-10-01T10:00:00'), yue(2, '2026-10-01T11:00:00'), yue(3, '2026-09-01T10:00:00')]; store.historyLoaded = true }
  app = createApp({ render: () => kind === 'ace' ? h(ResultsFeed) : h(TrackFeed) }).use(pinia).use(i18n)
  app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' })
  app.mount(document.body.appendChild(document.createElement('div'))); await flush()
}
function favoriteFilter(): HTMLInputElement {
  const checkbox = [...document.querySelectorAll('label')].find((label) => label.textContent?.includes('Favorites only'))?.querySelector('input')
  if (!checkbox) throw new Error('Missing favorites filter')
  return checkbox
}
function button(text: string): HTMLButtonElement { const button = [...document.querySelectorAll('button')].find((button) => button.textContent?.trim() === text); if (!button) throw new Error(`Missing ${text}`); return button }
it('filters individual ACE batch candidates, keeps their original fallback index, and omits whole-batch playback', async () => {
  await mount('ace')
  expect(document.querySelectorAll('[data-player]')).toHaveLength(2)
  favoriteFilter().click(); await flush()
  const players = document.querySelectorAll('[data-player]')
  expect(players).toHaveLength(1)
  expect(players[0]?.getAttribute('data-track')).toBe('2')
  expect(players[0]?.getAttribute('data-fallback')).toBe('/second.wav')
  expect(document.querySelector('[data-batch-player]')).toBeNull()
  expect([...document.querySelectorAll('[data-stems],[data-midi]')].map((element) => element.getAttribute('data-track'))).toEqual(['2', '2'])
  expect(document.body.textContent).toContain('Showing 1 of 2')
})
it('combines YuE favorites with dates and reset clears both filters', async () => {
  await mount('yue')
  favoriteFilter().click(); await flush()
  expect(document.querySelectorAll('[data-player]')).toHaveLength(2)
  const from = document.querySelector<HTMLInputElement>('input[type=date]')
  if (!from) throw new Error('Missing date filter')
  from.value = '2026-10-01'; from.dispatchEvent(new Event('change', { bubbles: true })); await flush()
  expect(document.querySelectorAll('[data-player]')).toHaveLength(1)
  expect(document.body.textContent).toContain('Showing 1 of 3')
  button('Reset').click(); await flush()
  expect(favoriteFilter().checked).toBe(false)
  expect(from.value).toBe('')
  expect(document.querySelectorAll('[data-player]')).toHaveLength(3)
})
it.each(['ace', 'yue'] as const)('exposes saved-track stars while the %s list row stays collapsed', async (kind) => {
  useTrackView().setView('list')
  await mount(kind)
  expect(document.querySelectorAll('[data-player]')).toHaveLength(0)
  const stars = document.querySelectorAll('button[aria-pressed]')
  expect(stars).toHaveLength(kind === 'ace' ? 2 : 3)
  expect([...stars].filter((star) => star.getAttribute('aria-pressed') === 'true')).toHaveLength(kind === 'ace' ? 1 : 2)
})
it('removes an unstarred favorite from the filtered feed after the save succeeds', async () => {
  await mount('yue')
  favoriteFilter().click(); await flush()
  const star = [...document.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')].find((button) => button.getAttribute('aria-label') === 'Remove Song 2 from favorites')
  if (!star) throw new Error('Missing saved favorite')
  star.click(); await flush()
  expect(api.setTrackFavorite).toHaveBeenCalledWith(2, false, expect.any(AbortSignal))
  expect(document.querySelectorAll('[data-player]')).toHaveLength(1)
  expect(document.querySelector('[data-player]')?.getAttribute('data-track')).toBe('3')
})
