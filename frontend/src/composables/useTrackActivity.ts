import { onBeforeUnmount, onMounted, ref } from 'vue'
import { listActiveAudioTrackIds } from '../api/trackActivity'
import { createPollingLoop } from './polling'

/** Discover existing conversion/export jobs even when their cards are on another page. */
export function useTrackActivity() {
  const activeTrackIds = ref<ReadonlySet<number>>(new Set())
  const error = ref(false)
  const poll = createPollingLoop(async context => {
    try {
      const ids = await listActiveAudioTrackIds(context.signal)
      if (!context.isCurrent()) return false
      activeTrackIds.value = new Set(ids); error.value = false
    } catch {
      if (context.isCurrent()) error.value = true
    }
  }, 2500)
  onMounted(() => poll.start())
  onBeforeUnmount(() => poll.stop())
  return { activeTrackIds, error }
}
