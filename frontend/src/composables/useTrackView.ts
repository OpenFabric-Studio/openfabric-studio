import { ref } from 'vue'

const STORAGE_KEY = 'openfabric_track_view'
export type TrackView = 'cards' | 'list'

const view = ref<TrackView>(readView())

function readView(): TrackView {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'list' ? 'list' : 'cards'
  } catch {
    return 'cards'
  }
}

export function useTrackView() {
  function setView(next: TrackView) {
    view.value = next
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // The toggle still works for this page if storage is blocked.
    }
  }
  return { view, setView }
}
