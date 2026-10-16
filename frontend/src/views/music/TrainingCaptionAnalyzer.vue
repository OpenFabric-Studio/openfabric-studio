<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as api from '../../api/trainingCaptions'
import type { MusicLoraSource, TrainingCaptionJob, TrainingCaptionRequest } from '../../api/generated'
import { createPollingLoop } from '../../composables/polling'

const props = defineProps<{ samples: readonly (MusicLoraSource & { title: string; caption: string })[]; segmentSeconds: number; disabled?: boolean }>()
const emit = defineEmits<{ caption: [value: { source: MusicLoraSource; caption: string }]; busy: [value: boolean] }>()
const { t } = useI18n()
const automatic = ref(true), known = ref(false), working = ref(false), reconnecting = ref(false), error = ref('')
const job = ref<TrainingCaptionJob | null>(null), pending = ref<TrainingCaptionRequest | null>(null)
const restored = ref(0), savedCaptions = new Map<string, string>()
const attempted = new Set<string>(), applied = new Set<string>()
let alive = true, visible = true, generation = 0, controller: AbortController | undefined
function sourceKey(source: MusicLoraSource): string { return source.dataset ? `${source.dataset.dataset_id}:${source.dataset.revision}:${source.dataset.sample_id}` : `track:${source.track_id}` }
function attemptKey(source: MusicLoraSource): string { return `${props.segmentSeconds}:${sourceKey(source)}` }
function sourceOnly(source: MusicLoraSource): MusicLoraSource { return source.dataset ? { dataset: { ...source.dataset } } : { track_id: source.track_id } }
function sameRequest(left: TrainingCaptionRequest, right: TrainingCaptionRequest): boolean {
  return left.segment_seconds === right.segment_seconds && left.samples.length === right.samples.length && left.samples.every((sample, index) => { const source = right.samples[index]; return source !== undefined && sourceKey(sample) === sourceKey(source) })
}
const active = computed(() => job.value?.status === 'queued' || job.value?.status === 'running')
const busy = computed(() => working.value || active.value || pending.value !== null)
const missing = computed(() => props.samples.filter(sample => !sample.caption.trim()))
const finished = computed(() => job.value?.items.filter(item => item.status === 'done' || item.status === 'failed').length ?? 0)
const currentTitle = computed(() => {
  const source = job.value?.items.find(item => item.status === 'running')?.source
  return source ? props.samples.find(sample => sourceKey(sample) === sourceKey(source))?.title ?? t('musicLora.captionAnalysis.recording') : ''
})
const jobError = computed(() => job.value?.status === 'cancelled' ? null : job.value?.error_code)
const failedTitles = computed(() => job.value?.items.filter(item => item.status === 'failed' && item.error_code !== 'training_caption_cancelled').map(item => props.samples.find(sample => sourceKey(sample) === sourceKey(item.source))?.title ?? t('musicLora.captionAnalysis.recording')) ?? [])
watch(busy, value => emit('busy', value), { immediate: true })
function applyResults() {
  const result = job.value
  if (!visible || !result || result.request.segment_seconds !== props.segmentSeconds) return
  for (const item of result.items) {
    const key = `${result.id}:${sourceKey(item.source)}`
    const caption = item.caption
    if (item.status !== 'done' || !caption?.trim() || applied.has(key)) continue
    const selected = props.samples.find(sample => sourceKey(sample) === sourceKey(item.source))
    if (!selected) continue
    applied.add(key)
    if (!selected.caption.trim()) emit('caption', { source: item.source, caption })
  }
}
function applySaved() {
  if (!visible || props.disabled || !automatic.value) return
  for (const sample of missing.value) {
    const key = attemptKey(sample), caption = savedCaptions.get(key)
    if (!caption || attempted.has(key)) continue
    attempted.add(key); restored.value++
    emit('caption', { source: sourceOnly(sample), caption })
  }
}
function accept(value: TrainingCaptionJob) {
  for (const source of value.request.samples) attempted.add(`${value.request.segment_seconds}:${sourceKey(source)}`)
  job.value = value; known.value = true; pending.value = null; reconnecting.value = false; error.value = ''; applyResults()
}
function safeError(cause: unknown): string {
  const code = cause instanceof ApiError ? cause.message : ''
  if (code === 'training_caption_stop_failed') return t('musicLora.captionAnalysis.stopFailed')
  return t(['native_model_busy', 'music_queue_busy', 'module_setup_busy', 'training_caption_busy'].includes(code) ? 'musicLora.captionAnalysis.busy' : code === 'training_caption_setup_required' ? 'musicLora.captionAnalysis.setup' : 'musicLora.captionAnalysis.error')
}
const poll = createPollingLoop(async context => {
  try {
    if (job.value && active.value) {
      const result = await api.getTrainingCaptions(job.value.id, context.signal)
      if (!alive || !visible || !context.isCurrent()) return false
      accept(result)
    } else {
      const response = await api.listTrainingCaptions(context.signal)
      if (!alive || !visible || !context.isCurrent()) return false
      savedCaptions.clear()
      for (const result of response.jobs) for (const item of result.items) {
        const key = `${result.request.segment_seconds}:${sourceKey(item.source)}`
        if (item.status === 'done' && item.caption?.trim() && !savedCaptions.has(key)) savedCaptions.set(key, item.caption)
      }
      const request = pending.value
      const matching = request ? response.jobs.find(item => sameRequest(item.request, request)) : undefined
      const running = response.jobs.find(item => item.status === 'queued' || item.status === 'running')
      if (matching) accept(matching)
      if (running && running.id !== matching?.id) accept(running)
      if (!matching && !running) { pending.value = null; reconnecting.value = false; if (request && !error.value) error.value = t('musicLora.captionAnalysis.error') }
      applySaved()
      known.value = true
    }
  } catch { if (alive && visible && context.isCurrent()) reconnecting.value = true }
  return active.value || pending.value !== null || !known.value || reconnecting.value
}, 1500)
async function analyze(manual = false) {
  if (!alive || !visible || props.disabled || busy.value || !known.value) return
  const selected = missing.value.filter(sample => manual || !attempted.has(attemptKey(sample)))
  if (!selected.length || !Number.isInteger(props.segmentSeconds) || props.segmentSeconds < 6 || props.segmentSeconds > 30) return
  const request: TrainingCaptionRequest = { samples: selected.map(sourceOnly), segment_seconds: props.segmentSeconds }
  for (const sample of selected) attempted.add(attemptKey(sample))
  const operation = new AbortController(), token = ++generation
  controller = operation; working.value = true; error.value = ''; job.value = null; pending.value = request; poll.stop()
  try {
    const result = await api.submitTrainingCaptions(request, operation.signal)
    if (alive && visible && generation === token) accept(result)
  } catch (cause) {
    if (alive && visible && generation === token) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) { pending.value = null; error.value = safeError(cause) }
      else { pending.value = request; reconnecting.value = true; if (cause instanceof ApiError) error.value = safeError(cause) }
    }
  } finally {
    if (alive && visible && generation === token) { working.value = false; controller = undefined; if (active.value || pending.value) poll.start(false) }
  }
}
async function cancel() {
  if (!job.value || working.value) return
  automatic.value = false; poll.stop()
  const operation = new AbortController(), token = ++generation
  controller = operation; working.value = true; error.value = ''
  try { const result = await api.cancelTrainingCaptions(job.value.id, operation.signal); if (alive && visible && generation === token) accept(result) }
  catch (cause) { if (alive && visible && generation === token) { error.value = safeError(cause); reconnecting.value = true } }
  finally { if (alive && visible && generation === token) { working.value = false; controller = undefined; if (active.value || reconnecting.value) poll.start(false) } }
}
watch(() => [props.samples.map(sample => `${sourceKey(sample)}:${sample.caption}`).join('\n'), props.segmentSeconds, props.disabled, automatic.value, known.value, busy.value], () => { applyResults(); applySaved(); if (automatic.value) void analyze() }, { flush: 'post' })
onMounted(() => poll.start())
onActivated(() => { visible = true; if (!known.value || active.value || pending.value) poll.start(); else { applyResults(); if (automatic.value) void analyze() } })
onDeactivated(() => { visible = false; ++generation; controller?.abort(); controller = undefined; working.value = false; known.value = false; poll.stop() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <section data-caption-analysis class="space-y-3 rounded-lg border border-border bg-panel-2 p-4" :aria-label="t('musicLora.captionAnalysis.title')" :aria-busy="busy">
    <h4 class="font-medium text-text">{{ t('musicLora.captionAnalysis.title') }}</h4>
    <label class="flex min-h-11 items-center gap-2 text-sm text-text"><input v-model="automatic" type="checkbox" />{{ t('musicLora.captionAnalysis.automatic') }}</label>
    <p class="text-sm text-text-dim">{{ t('musicLora.captionAnalysis.hint', { seconds: segmentSeconds }) }}</p>
    <p v-if="disabled && missing.length" role="status" class="text-sm text-text-dim">{{ t('musicLora.captionAnalysis.waiting') }}</p>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="caption-button" :disabled="disabled || busy || !known || !missing.length" @click="analyze(true)">{{ t('musicLora.captionAnalysis.analyze') }}</button>
      <button v-if="active" type="button" class="caption-button" :disabled="working" @click="cancel">{{ t('musicLora.captionAnalysis.cancel') }}</button>
    </div>
    <p v-if="working" role="status" class="text-sm text-text-dim">{{ t('musicLora.captionAnalysis.starting') }}</p>
    <div v-if="restored" role="status" class="space-y-1 text-sm text-text-dim"><p>{{ t('musicLora.captionAnalysis.restored', { count: restored }) }}</p><p>{{ t('musicLora.captionAnalysis.review') }}</p></div>
    <div v-if="job" role="status" class="space-y-1 text-sm text-text-dim">
      <p>{{ t(`musicLora.captionAnalysis.states.${job.status}`) }} · {{ finished }} / {{ job.items.length }}</p>
      <p v-if="job.status === 'running' && job.items.every(item => item.status === 'queued')">{{ t('musicLora.captionAnalysis.loading') }}</p>
      <progress v-if="active" :value="finished" :max="job.items.length" :aria-label="t('musicLora.captionAnalysis.title')" class="w-full accent-accent1"></progress>
      <p v-if="currentTitle" class="break-words">{{ currentTitle }}</p>
      <p v-if="job.items.some(item => item.status === 'done')">{{ t('musicLora.captionAnalysis.review') }}</p>
    </div>
    <p v-if="reconnecting" role="status" class="text-sm text-status-queued">{{ t('musicLora.captionAnalysis.reconnecting') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="jobError" role="alert" class="text-sm text-status-queued">{{ safeError(new ApiError(jobError, 503)) }}</p>
    <p v-for="(title, index) in failedTitles" :key="index" class="break-words text-sm text-status-queued">{{ t('musicLora.captionAnalysis.failed', { title }) }}</p>
    <RouterLink v-if="error || jobError || failedTitles.length" to="/settings#module-ace_step" class="inline-flex min-h-11 items-center text-sm text-accent1 underline">{{ t('musicLora.captionAnalysis.openSetup') }}</RouterLink>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.caption-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
