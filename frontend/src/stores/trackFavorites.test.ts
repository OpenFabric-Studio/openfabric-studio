// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import * as api from '../api/tracks'
import type { SavedTrack } from '../api/tracks'
import { useTrackFavoritesStore } from './trackFavorites'

vi.mock('../api/tracks', async (original) => ({ ...await original<typeof import('../api/tracks')>(), listTracks: vi.fn(), setTrackFavorite: vi.fn() }))
export function favoriteTrackFixture(id: number, isFavorite = false): SavedTrack {
  return { id, short_id: id, model: 'ace_step', title: `Song ${id}`, filename: `${id}.wav`, created_at: '2026-10-01T10:00:00Z', lyrics: '', seed: 1, duration_ms: 4000, wall_ms: null, params: {}, audio_url: `/api/tracks/${id}/audio`, abc_url: null, stems: null, midi: null, is_favorite: isFavorite }
}
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(api.listTracks).mockResolvedValue([favoriteTrackFixture(1), favoriteTrackFixture(2, true)])
  vi.mocked(api.setTrackFavorite).mockImplementation(async (id, favorite) => favoriteTrackFixture(id, favorite))
})
it('hydrates saved favorites and restores them in a fresh app store', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  expect(store.hasTrack(1)).toBe(true)
  expect(store.isFavorite(1)).toBe(false)
  expect(store.isFavorite(2)).toBe(true)
  setActivePinia(createPinia())
  const reloaded = useTrackFavoritesStore()
  await reloaded.ensureLoaded()
  expect(reloaded.isFavorite(2)).toBe(true)
})
it('coalesces hydration for several track controls', async () => {
  const store = useTrackFavoritesStore()
  await Promise.all([store.ensureLoaded(), store.ensureLoaded(), store.ensureLoaded()])
  expect(api.listTracks).toHaveBeenCalledTimes(1)
})
it('keeps the displayed favorite until its confirmed mutation completes and blocks duplicate toggles', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  let release: (track: SavedTrack) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.setTrackFavorite).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = store.toggle(1)
  expect(store.isPending(1)).toBe(true)
  expect(store.isFavorite(1)).toBe(false)
  await store.toggle(1)
  expect(api.setTrackFavorite).toHaveBeenCalledTimes(1)
  release(favoriteTrackFixture(1, true))
  await pending
  expect(store.isFavorite(1)).toBe(true)
  expect(store.isPending(1)).toBe(false)
})
it('preserves the last authoritative state when toggling fails and permits retry', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  vi.mocked(api.setTrackFavorite).mockRejectedValueOnce(new Error('private server trace'))
  expect(await store.toggle(2)).toBe(false)
  expect(store.isFavorite(2)).toBe(true)
  expect(store.errorFor(2)).toBe(true)
  expect(store.isPending(2)).toBe(false)
  expect(await store.toggle(2)).toBe(true)
  expect(store.isFavorite(2)).toBe(false)
  expect(store.errorFor(2)).toBe(false)
})
it('does not let an old inventory response overwrite a completed favorite mutation', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  let release: (tracks: SavedTrack[]) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.listTracks).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const refresh = store.refresh()
  await store.toggle(1)
  release([favoriteTrackFixture(1, false), favoriteTrackFixture(2, false)])
  await refresh
  expect(store.isFavorite(1)).toBe(true)
  expect(store.isFavorite(2)).toBe(false)
})
it('rejects an inventory snapshot taken while the favorite mutation was still pending', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  let finishToggle: (track: SavedTrack) => void = () => { throw new Error('Not initialized') }
  let finishList: (tracks: SavedTrack[]) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.setTrackFavorite).mockReturnValueOnce(new Promise((resolve) => { finishToggle = resolve }))
  const toggle = store.toggle(1)
  vi.mocked(api.listTracks).mockReturnValueOnce(new Promise((resolve) => { finishList = resolve }))
  const refresh = store.refresh()
  finishToggle(favoriteTrackFixture(1, true)); await toggle
  finishList([favoriteTrackFixture(1)]); await refresh
  expect(store.isFavorite(1)).toBe(true)
})
it('ignores a mutation response after its control has been aborted', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  const controller = new AbortController()
  let release: (track: SavedTrack) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.setTrackFavorite).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = store.toggle(1, controller.signal)
  controller.abort()
  release(favoriteTrackFixture(1, true))
  await pending
  expect(store.isFavorite(1)).toBe(false)
  expect(store.errorFor(1)).toBe(false)
  expect(store.isPending(1)).toBe(false)
})
it('does not assign a response for a different saved track to the requested track', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  vi.mocked(api.setTrackFavorite).mockResolvedValueOnce(favoriteTrackFixture(2, false))
  expect(await store.toggle(1)).toBe(false)
  expect(store.isFavorite(1)).toBe(false)
  expect(store.isFavorite(2)).toBe(true)
  expect(store.errorFor(1)).toBe(true)
})
it('reports hydration failure without treating unknown favorites as known false values', async () => {
  const store = useTrackFavoritesStore()
  vi.mocked(api.listTracks).mockRejectedValueOnce(new Error('private server trace'))
  expect(await store.ensureLoaded()).toBe(false)
  expect(store.loaded).toBe(false)
  expect(store.loadError).toBe(true)
  expect(store.hasTrack(1)).toBe(false)
  expect(await store.ensureLoaded()).toBe(true)
  expect(store.loadError).toBe(false)
})
it('loads a newly saved track after an older in-flight inventory that did not contain it', async () => {
  const store = useTrackFavoritesStore()
  let release: (tracks: SavedTrack[]) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.listTracks).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const old = store.refresh()
  const newTrack = store.ensureTrack(3)
  vi.mocked(api.listTracks).mockResolvedValue([favoriteTrackFixture(1), favoriteTrackFixture(3, true)])
  release([favoriteTrackFixture(1)])
  await old
  expect(await newTrack).toBe(true)
  expect(store.isFavorite(3)).toBe(true)
  expect(api.listTracks).toHaveBeenCalledTimes(2)
})
it('reconciles an aborted toggle from a fresh authoritative inventory after an older refresh', async () => {
  const store = useTrackFavoritesStore()
  await store.ensureLoaded()
  let finishOld: (tracks: SavedTrack[]) => void = () => { throw new Error('Not initialized') }
  let finishToggle: (track: SavedTrack) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.listTracks).mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve }))
  vi.mocked(api.setTrackFavorite).mockReturnValueOnce(new Promise((resolve) => { finishToggle = resolve }))
  const old = store.refresh()
  const controller = new AbortController()
  const toggle = store.toggle(1, controller.signal)
  controller.abort()
  vi.mocked(api.listTracks).mockResolvedValue([favoriteTrackFixture(1, true)])
  finishToggle(favoriteTrackFixture(1, true)); await toggle
  finishOld([favoriteTrackFixture(1)]); await old
  await store.ensureLoaded()
  for (let i = 0; i < 5; i++) await Promise.resolve()
  expect(store.isFavorite(1)).toBe(true)
  expect(api.listTracks).toHaveBeenCalledTimes(3)
})
