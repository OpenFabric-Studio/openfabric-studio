<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/audiobookReview'
import type { AsrCapability, AsrReview } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
const props = defineProps<{ bookId: string; chapterIndex: number; passageId: string; revision: number; renderIdentity: string; active: boolean }>()
const { t } = useI18n()
const review = ref<AsrReview | null>(null), capability = ref<AsrCapability | null>(null)
const busy = ref(false), error = ref('')
const restoring = ref(false)
let alive = true, generation = 0
let controller: AbortController | undefined
const running = computed(() => review.value?.state === 'queued' || review.value?.state === 'running')
function current(result: AsrReview) { return result.book_id === props.bookId && result.chapter_index === props.chapterIndex && result.passage_id === props.passageId && result.revision === props.revision && result.render_identity === props.renderIdentity }
const polling = createPollingLoop(async ({ signal, isCurrent }) => {
  const target = review.value
  if (!target || !props.active) return
  const token = generation
  try {
    const result = await api.getAsrReview(target.id, signal)
    if (alive && token === generation && isCurrent() && current(result)) review.value = result
    if (!running.value) polling.stop()
  } catch { if (alive && token === generation && isCurrent()) { error.value = t('audiobookReview.qaFailed'); polling.stop() } }
}, 2000)
watch(() => [props.bookId, props.chapterIndex, props.passageId, props.revision, props.renderIdentity, props.active] as const, async () => {
  const token = ++generation; controller?.abort(); polling.stop(); busy.value = false; review.value = null; error.value = ''; capability.value = null; restoring.value = props.active
  if (!props.active) return
  const request = new AbortController(); controller = request
  try {
    const [available, rows] = await Promise.all([api.getAsrCapability(request.signal), api.listAsrReviews(props.bookId, request.signal)])
    if (!alive || token !== generation || request.signal.aborted) return
    capability.value = available
    review.value = rows.filter(current).sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
    if (running.value) polling.start()
  } catch { if (alive && token === generation && !request.signal.aborted) error.value = t('audiobookReview.qaFailed') }
  finally { if (alive && token === generation && !request.signal.aborted) restoring.value = false }
}, { immediate: true })
async function run(cancel = false) {
  if (busy.value || restoring.value || !props.active || !cancel && !capability.value?.available || cancel && !review.value) return
  const token = generation, request = new AbortController(); controller?.abort(); controller = request; busy.value = true; error.value = ''; polling.stop()
  try {
    const result = cancel && review.value
      ? await api.cancelAsrReview(review.value.id, request.signal)
      : await api.checkPassage(props.bookId, props.chapterIndex, props.passageId, { revision: props.revision, render_identity: props.renderIdentity }, request.signal)
    if (!alive || token !== generation || request.signal.aborted || !current(result)) return
    review.value = result
    if (running.value) polling.start()
  } catch { if (alive && token === generation && !request.signal.aborted) error.value = t('audiobookReview.qaFailed') }
  finally { if (alive && token === generation && !request.signal.aborted) busy.value = false }
}
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); polling.stop() })
</script>
<template>
  <section class="space-y-3 rounded-lg border border-border p-3" :aria-label="t('audiobookReview.qa')">
    <p class="text-xs text-text-dim">{{ t('audiobookReview.qaHint') }}</p>
    <p v-if="capability && !capability.available" class="text-xs text-status-queued">{{ t('audiobookReview.qaUnavailable') }}</p>
    <div class="flex flex-wrap items-center gap-3">
      <button type="button" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" :disabled="busy || restoring || running || !active || !capability?.available" @click="run()">{{ t('audiobookReview.qa') }}</button>
      <button v-if="running" type="button" class="min-h-11 px-3 text-xs text-text-dim" :disabled="busy" @click="run(true)">{{ t('audiobookReview.qaCancel') }}</button>
      <span v-if="running" role="status" class="text-xs text-text-dim">{{ t('audiobookReview.qaPending') }}</span>
    </div>
    <p v-if="error" role="alert" class="text-xs text-status-failed">{{ error }}</p>
    <template v-if="review && !running">
      <p v-if="review.state !== 'completed'" role="status" class="text-xs text-text-dim">{{ t(`audiobookReview.status.${review.state}`) }}</p>
      <template v-else>
        <ul v-if="review.flags?.length" class="list-disc space-y-1 pl-5 text-xs text-status-queued"><li v-for="flag in review.flags" :key="flag.code + flag.message">{{ flag.message }}</li></ul>
        <p v-else class="text-xs text-status-done">{{ t('audiobookReview.qaClean') }}</p>
        <details><summary class="cursor-pointer text-xs text-text-dim">{{ t('audiobookReview.qaTranscript') }}</summary><p class="mt-2 whitespace-pre-wrap text-sm text-text">{{ review.transcript }}</p></details>
      </template>
    </template>
  </section>
</template>
