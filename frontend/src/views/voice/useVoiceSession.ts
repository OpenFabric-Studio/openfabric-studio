import { onBeforeUnmount, watch } from 'vue'

/** Requests belong to one mounted voice, including POST/PATCH responses after navigation. */
export function useVoiceSession(voiceId: () => string) {
  let alive = true
  let generation = 0
  let controller = new AbortController()
  function invalidate() {
    generation++
    controller.abort()
    controller = new AbortController()
  }
  watch(voiceId, invalidate, { flush: 'sync' })
  onBeforeUnmount(() => { alive = false; invalidate() })
  return () => {
    const id = voiceId()
    const token = generation
    return { id, signal: controller.signal, isCurrent: () => alive && generation === token && id === voiceId() }
  }
}
