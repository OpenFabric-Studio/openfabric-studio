<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { RetainedAudioInfo, RetainedAudioSource } from '../api/contracts'
import { retainedAudioInfo, retainedAudioVideo } from '../api/readingMedia'

const props = withDefaults(defineProps<{ source: RetainedAudioSource; disabled?: boolean; active?: boolean }>(), { active: true })
const emit = defineEmits<{ 'open-video': [projectId: string] }>()
const { t } = useI18n()
const info = ref<RetainedAudioInfo | null>(null)
const start = ref<number | string>(0), end = ref<number | string>('')
const working = ref(false), error = ref(''), projectId = ref('')
let alive = true, generation = 0, controller: AbortController | null = null
function reset() {
  generation++; controller?.abort(); controller = null
  info.value = null; error.value = ''; projectId.value = ''; working.value = false
  start.value = 0; end.value = ''
}
async function inspect() {
  if (!props.active || props.disabled || working.value) return
  const token = generation, request = new AbortController(); controller = request
  working.value = true; error.value = ''
  try {
    const result = await retainedAudioInfo(props.source, request.signal)
    if (!alive || token !== generation) return
    info.value = result
    end.value = result.duration_ms <= 15000 ? result.duration_ms / 1000 : ''
  } catch {
    if (alive && token === generation && !request.signal.aborted) error.value = 'readingMedia.handoffError'
  } finally {
    if (alive && token === generation) { working.value = false; controller = null }
  }
}
async function create() {
  const selected = info.value
  if (!props.active || !selected || props.disabled || working.value) return
  const startMs = Math.round(Number(start.value) * 1000)
  const endMs = end.value === '' ? null : Math.round(Number(end.value) * 1000)
  if (!Number.isFinite(startMs) || startMs < 0 || endMs === null || !Number.isFinite(endMs)
    || endMs > selected.duration_ms || endMs - startMs < 200 || endMs - startMs > 15000) {
    error.value = 'readingMedia.invalidBounds'; return
  }
  const token = generation, request = new AbortController(); controller = request
  working.value = true; error.value = ''
  try {
    const result = await retainedAudioVideo({ source: selected.source, source_sha256: selected.source_sha256,
      name: t('readingMedia.handoffTitle'), clip_start_ms: startMs, clip_end_ms: endMs }, request.signal)
    if (alive && token === generation) projectId.value = result.id
  } catch {
    if (alive && token === generation && !request.signal.aborted) error.value = 'readingMedia.handoffError'
  } finally {
    if (alive && token === generation) { working.value = false; controller = null }
  }
}
watch(() => [props.source.kind, props.source.source_id, props.source.chapter_index, props.source.revision, props.active], reset, { flush: 'sync' })
onBeforeUnmount(() => { alive = false; reset() })
</script>
<template>
  <details class="rounded-xl border border-border bg-panel p-4" data-retained-audio>
    <summary class="cursor-pointer font-semibold">{{ t('readingMedia.handoffTitle') }}</summary>
    <div class="mt-3 space-y-3">
      <p class="text-sm text-text-dim">{{ t('readingMedia.handoffHint') }}</p>
      <button v-if="!info" type="button" data-inspect :disabled="working || disabled" class="rounded border border-border px-3 py-2 text-sm" @click="inspect">{{ t('readingMedia.inspect') }}</button>
      <template v-if="info">
        <p class="text-sm">{{ t('readingMedia.duration', { seconds: (info.duration_ms / 1000).toFixed(2) }) }}</p>
        <p class="text-xs text-text-dim">{{ t('readingMedia.bounds') }}</p>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="block text-sm">{{ t('readingMedia.start') }}<input v-model="start" data-start type="number" min="0" :max="info.duration_ms / 1000" step="0.01" :disabled="working || disabled" class="mt-1 w-full rounded border border-border bg-panel-2 p-2" /></label>
          <label class="block text-sm">{{ t('readingMedia.end') }}<input v-model="end" data-end type="number" min="0.2" :max="info.duration_ms / 1000" step="0.01" :disabled="working || disabled" class="mt-1 w-full rounded border border-border bg-panel-2 p-2" /></label>
        </div>
        <button type="button" data-create :disabled="working || disabled || Boolean(projectId)" class="rounded bg-accent1 px-3 py-2 text-sm text-white" @click="create">{{ t(working ? 'readingMedia.creating' : 'readingMedia.create') }}</button>
      </template>
      <p v-if="error" role="alert" class="text-sm text-status-failed">{{ t(error) }}</p>
      <button v-if="projectId" type="button" data-open class="text-sm text-accent1" @click="emit('open-video', projectId)">{{ t('readingMedia.open') }}</button>
    </div>
  </details>
</template>
