<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ReadAlongExport } from '../api/contracts'
import { cancelReadAlong, createReadAlong, getReadAlong, listReadAlong, resumeReadAlong } from '../api/readingMedia'
import { ApiError } from '../api/http'
import VideoPreviewPlayer from '../views/video/VideoPreviewPlayer.vue'

const props = withDefaults(defineProps<{ bookId: string; chapterIndex: number; revision: number; disabled?: boolean; active?: boolean }>(), { active: true })
const { t } = useI18n()
const exports = ref<ReadAlongExport[]>([])
const aspect = ref<'portrait' | 'landscape'>('portrait')
const working = ref(false), error = ref('')
const active = computed(() => exports.value.find(item => item.status === 'queued' || item.status === 'running'))
const previewExport = computed(() => exports.value.find(item => item.status === 'done' && item.preview_seconds != null))
const expanded = ref(false)
function toggle(event: Event) { if (event.target instanceof HTMLDetailsElement) expanded.value = event.target.open }
let alive = true, generation = 0
let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | null = null
function clear() {
  controller?.abort(); controller = null
  if (timer !== null) clearTimeout(timer)
  timer = null
}
function upsert(result: ReadAlongExport) {
  exports.value = [result, ...exports.value.filter(item => item.id !== result.id)]
}
function schedule(token: number) {
  if (!alive || !props.active || token !== generation || !active.value) return
  timer = setTimeout(() => { void poll(token) }, 1000)
}
async function poll(token: number) {
  timer = null
  const selected = active.value
  if (!alive || token !== generation || !selected || working.value) { schedule(token); return }
  const request = new AbortController(); controller = request
  try {
    const result = await getReadAlong(selected.id, request.signal)
    if (!alive || token !== generation || request.signal.aborted) return
    upsert(result)
  } catch (cause) {
    if (alive && token === generation && !request.signal.aborted) error.value = 'readingMedia.error'
  } finally {
    if (alive && token === generation && controller === request) { controller = null; schedule(token) }
  }
}
async function load() {
  clear()
  const token = ++generation
  const request = new AbortController(); controller = request
  error.value = ''; working.value = true
  try {
    const result = await listReadAlong(props.bookId, props.chapterIndex, request.signal)
    if (alive && token === generation) exports.value = result
  } catch {
    if (alive && token === generation && !request.signal.aborted) error.value = 'readingMedia.error'
  } finally {
    if (alive && token === generation) { working.value = false; controller = null; schedule(token) }
  }
}
async function action(kind: 'preview' | 'export' | 'cancel' | 'resume', exportId?: string) {
  if (!props.active || working.value || (props.disabled && kind !== 'cancel')) return
  clear()
  const token = ++generation
  const request = new AbortController(); controller = request
  working.value = true; error.value = ''
  try {
    const result = kind === 'cancel' && exportId ? await cancelReadAlong(exportId, request.signal)
      : kind === 'resume' && exportId ? await resumeReadAlong(exportId, request.signal)
      : await createReadAlong(props.bookId, props.chapterIndex, {
        revision: props.revision, aspect: aspect.value, preview_seconds: kind === 'preview' ? 25 : null,
      }, request.signal)
    if (alive && token === generation) upsert(result)
  } catch (cause) {
    if (alive && token === generation && !request.signal.aborted) {
      error.value = cause instanceof ApiError && cause.message === 'reading_source_changed'
        ? 'readingMedia.sourceChanged' : 'readingMedia.unavailable'
    }
  } finally {
    if (alive && token === generation) { working.value = false; controller = null; schedule(token) }
  }
}
function link(item: ReadAlongExport, name: 'movie.mp4' | 'captions.srt' | 'captions.vtt' | 'manifest.json'): string {
  return `/api/reading-media/exports/${item.id}/${name}`
}
watch(() => [props.bookId, props.chapterIndex, props.revision, props.active], () => {
  exports.value = []; clear(); generation++; working.value = false
  if (props.active) void load()
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { alive = false; generation++; clear() })
</script>

<template>
  <details class="rounded-xl border border-border bg-panel p-4" data-read-along @toggle="toggle">
    <summary class="cursor-pointer font-semibold">{{ t('readingMedia.title') }}</summary>
    <div class="mt-3 space-y-3">
      <p class="text-sm text-text-dim">{{ t('readingMedia.hint') }}</p>
      <label class="block text-sm">{{ t('readingMedia.aspect') }}
        <select v-model="aspect" :disabled="working || Boolean(active) || disabled" class="mt-1 rounded border border-border bg-panel-2 p-2">
          <option value="portrait">{{ t('readingMedia.portrait') }}</option>
          <option value="landscape">{{ t('readingMedia.landscape') }}</option>
        </select>
      </label>
      <div class="flex flex-wrap gap-2">
        <button type="button" data-preview :disabled="working || Boolean(active) || disabled" class="rounded border border-border px-3 py-2 text-sm" @click="action('preview')">{{ t('readingMedia.preview') }}</button>
        <button type="button" data-export :disabled="working || Boolean(active) || disabled" class="rounded bg-accent1 px-3 py-2 text-sm text-white" @click="action('export')">{{ t('readingMedia.export') }}</button>
        <button type="button" :disabled="working" class="rounded px-3 py-2 text-sm" @click="load">{{ t('readingMedia.reload') }}</button>
      </div>
      <p v-if="error" role="alert" class="text-sm text-status-failed">{{ t(error) }}</p>
      <VideoPreviewPlayer v-if="props.active && expanded && previewExport" :key="previewExport.id"
        :src="link(previewExport, 'movie.mp4')" :label="t('readingMedia.previewPlayer')" class="mx-auto max-w-sm" />
      <article v-for="item in exports.slice(0, 8)" :key="item.id" class="space-y-2 border-t border-border pt-3">
        <p class="text-sm" aria-live="polite">{{ t(`readingMedia.status.${item.status}`) }} · {{ t(item.preview_seconds == null ? 'readingMedia.chapterVideo' : 'readingMedia.previewVideo') }} · {{ (item.duration_ms / 1000).toFixed(1) }} s · {{ t(`readingMedia.${item.aspect}`) }}</p>
        <p v-if="item.source_revision !== revision" class="text-xs text-text-dim">{{ t('readingMedia.stale') }}</p>
        <p v-if="item.text_basis === 'spoken_fallback'" class="text-xs text-text-dim">{{ t('readingMedia.fallback') }}</p>
        <div v-if="item.status === 'done'" class="flex flex-wrap gap-x-4 gap-y-2 text-sm text-accent1">
          <a :href="link(item, 'movie.mp4')" download>{{ t('readingMedia.download') }}</a>
          <a :href="link(item, 'captions.srt')" download>{{ t('readingMedia.srt') }}</a>
          <a :href="link(item, 'captions.vtt')" download>{{ t('readingMedia.vtt') }}</a>
          <a :href="link(item, 'manifest.json')" download>{{ t('readingMedia.manifest') }}</a>
        </div>
        <button v-else-if="item.status === 'queued' || item.status === 'running'" type="button" :disabled="working" class="rounded border border-border px-3 py-2 text-sm" @click="action('cancel', item.id)">{{ t('readingMedia.cancel') }}</button>
        <template v-else>
          <p class="text-xs text-text-dim">{{ t(item.detail === 'reading_source_changed' ? 'readingMedia.sourceChanged' : 'readingMedia.error') }}</p>
          <button type="button" :disabled="working || Boolean(active) || disabled || item.source_revision !== revision" class="rounded border border-border px-3 py-2 text-sm" @click="action('resume', item.id)">{{ t('readingMedia.resume') }}</button>
        </template>
      </article>
    </div>
  </details>
</template>
