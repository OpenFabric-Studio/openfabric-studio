import { onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'
import { listTracks, setTrackFavorite } from '../api/tracks'

/** Saved-track IDs own favorite state; generator jobs and batch positions do not. */
export const useTrackFavoritesStore = defineStore('trackFavorites', () => {
  const favorites = ref(new Map<number, boolean>())
  const codes = ref(new Map<number, number | null>())
  const pending = ref(new Set<number>())
  const errors = ref(new Set<number>())
  const loaded = ref(false)
  const loading = ref(false)
  const loadError = ref(false)
  const changedAt = new Map<number, number>()
  const controllers = new Set<AbortController>()
  let changeVersion = 0
  let loadPromise: Promise<boolean> | undefined
  let alive = true

  function hasTrack(id: number): boolean { return favorites.value.has(id) }
  function isFavorite(id: number): boolean { return favorites.value.get(id) ?? false }
  function isPending(id: number): boolean { return pending.value.has(id) }
  function errorFor(id: number): boolean { return errors.value.has(id) }
  function trackCode(id: number): number | null | undefined { return codes.value.get(id) }

  function refresh(): Promise<boolean> {
    if (loadPromise) return loadPromise
    if (!alive) return Promise.resolve(false)
    const version = changeVersion
    const controller = new AbortController()
    controllers.add(controller)
    loading.value = true
    loadError.value = false
    loadPromise = (async () => {
      try {
        const tracks = await listTracks(undefined, controller.signal)
        if (!alive || controller.signal.aborted) return false
        const present = new Set(tracks.map((track) => track.id))
        for (const id of favorites.value.keys()) {
          if (!present.has(id) && !pending.value.has(id) && (changedAt.get(id) ?? 0) <= version) { favorites.value.delete(id); codes.value.delete(id) }
        }
        for (const track of tracks) {
          codes.value.set(track.id, track.short_id)
          if (!pending.value.has(track.id) && (changedAt.get(track.id) ?? 0) <= version) favorites.value.set(track.id, track.is_favorite ?? false)
        }
        loaded.value = true
        return true
      } catch {
        if (alive && !controller.signal.aborted) loadError.value = true
        return false
      } finally {
        controllers.delete(controller)
        loading.value = false
        loadPromise = undefined
      }
    })()
    return loadPromise
  }
  function ensureLoaded(): Promise<boolean> { return loaded.value ? Promise.resolve(true) : refresh() }
  async function ensureTrack(id: number): Promise<boolean> {
    if (hasTrack(id)) return true
    if (!await refresh()) return false
    // A newly saved track may have appeared after the coalesced request started.
    if (!hasTrack(id) && !await refresh()) return false
    return hasTrack(id)
  }
  async function reconcileAbort(): Promise<void> {
    if (loadPromise) await loadPromise
    if (alive) await refresh()
  }

  async function toggle(id: number, signal?: AbortSignal): Promise<boolean> {
    if (!alive || signal?.aborted || !hasTrack(id) || isPending(id)) return false
    const favorite = !isFavorite(id)
    const controller = new AbortController()
    const abort = () => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    controllers.add(controller)
    changedAt.set(id, ++changeVersion)
    pending.value.add(id)
    errors.value.delete(id)
    try {
      const track = await setTrackFavorite(id, favorite, controller.signal)
      if (!alive || controller.signal.aborted) return false
      if (track.id !== id || typeof track.is_favorite !== 'boolean') { errors.value.add(id); return false }
      favorites.value.set(id, track.is_favorite)
      codes.value.set(id, track.short_id)
      return true
    } catch {
      if (alive && !controller.signal.aborted) errors.value.add(id)
      return false
    } finally {
      signal?.removeEventListener('abort', abort)
      controllers.delete(controller)
      changedAt.set(id, ++changeVersion)
      pending.value.delete(id)
      if (alive && controller.signal.aborted) void reconcileAbort()
    }
  }

  onScopeDispose(() => { alive = false; for (const controller of controllers) controller.abort(); controllers.clear() })
  return { loaded, loading, loadError, hasTrack, isFavorite, isPending, errorFor, trackCode, refresh, ensureLoaded, ensureTrack, toggle }
})
