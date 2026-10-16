<script setup lang="ts">
import { nextTick, onBeforeUnmount, onDeactivated, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import { uploadTrack, type SavedTrack } from '../../api/tracks'
import { collectDroppedTrainingAudio, selectedTrainingAudio, TRAINING_AUDIO_ACCEPT, type TrainingAudioFilesResult } from '../../composables/trainingAudioFiles'

const props = defineProps<{ limit: number; selectedTrackIds: readonly number[]; disabled?: boolean }>()
const emit = defineEmits<{ pick: [track: { trackId: number; sourceLabel: string }]; busy: [value: boolean] }>()
const { t } = useI18n()
const filesInput = ref<HTMLInputElement | null>(null), folderInput = ref<HTMLInputElement | null>(null)
const busy = ref(false), reading = ref(false), dragging = ref(false), processed = ref(0), total = ref(0), filename = ref('')
const report = ref<ReturnType<typeof emptyReport> | null>(null)
const uploaded = new WeakMap<File, SavedTrack>()
let alive = true, generation = 0, controller: AbortController | undefined, uploading = false
function emptyReport() { return { added: 0, skipped: 0, duplicates: 0, limited: 0, unreadable: 0, truncated: false, cancelled: false, stopped: false, failed: new Array<string>() } }
function setBusy(value: boolean) { busy.value = value; emit('busy', value) }
function cancel() {
  if (!busy.value) return
  if (report.value) { report.value.cancelled = true; if (uploading && filename.value) report.value.failed.push(filename.value) }
  ++generation; controller?.abort(); controller = undefined; uploading = false; dragging.value = false; setBusy(false)
}
async function run(collect: (signal: AbortSignal) => Promise<TrainingAudioFilesResult>) {
  if (!alive || busy.value || props.disabled || props.limit < 1) return
  const request = new AbortController(), token = ++generation
  const current = () => alive && token === generation && !request.signal.aborted
  controller = request; report.value = emptyReport(); processed.value = 0; total.value = 0; filename.value = ''; reading.value = true; setBusy(true)
  try {
    const result = await collect(request.signal)
    if (!current()) return
    const summary = report.value
    Object.assign(summary, { skipped: result.skipped, unreadable: result.unreadable, truncated: result.truncated })
    reading.value = false; total.value = result.files.length
    const seen = new Set<File>()
    for (const [index, entry] of result.files.entries()) {
      if (!current()) return
      const saved = uploaded.get(entry.file)
      if (seen.has(entry.file) || saved && props.selectedTrackIds.includes(saved.id)) { summary.duplicates++; processed.value++; continue }
      seen.add(entry.file)
      if (props.limit < 1) { summary.limited = result.files.length - index; break }
      filename.value = entry.path
      try {
        uploading = !saved
        const track = saved ?? await uploadTrack(entry.file, undefined, request.signal)
        if (!current()) return
        uploading = false; uploaded.set(entry.file, track)
        await nextTick()
        if (!current()) return
        if (props.limit < 1) { summary.limited = result.files.length - index; break }
        emit('pick', { trackId: track.id, sourceLabel: track.title || entry.file.name })
        summary.added++
        await nextTick()
      } catch (cause) {
        if (!current()) return
        uploading = false; summary.failed.push(entry.path)
        if (!(cause instanceof ApiError) || cause.status < 400 || cause.status >= 500) { summary.stopped = true; break }
      }
      processed.value++
    }
  } catch {
    if (current() && report.value) { report.value.unreadable++; report.value.stopped = true }
  } finally {
    if (current()) { uploading = false; controller = undefined; filename.value = ''; setBusy(false) }
  }
}
function selected(event: Event) {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) return
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) void run(async () => selectedTrainingAudio(files))
}
function drop(event: DragEvent) {
  dragging.value = false
  const data = event.dataTransfer
  if (data) void run(signal => collectDroppedTrainingAudio(data, signal))
}
onDeactivated(cancel)
onBeforeUnmount(() => { cancel(); alive = false })
</script>

<template>
  <section :aria-label="t('musicLora.audioImport.title')" :aria-busy="busy" class="space-y-3 rounded-xl border-2 border-dashed p-4 transition-colors" :class="dragging && !disabled && !busy ? 'border-accent1 bg-accent1/10' : 'border-border'" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="drop">
    <p class="font-medium text-text">{{ t('musicLora.audioImport.title') }}</p>
    <p class="text-sm text-text-dim">{{ t('musicLora.audioImport.hint') }}</p>
    <input ref="filesInput" name="training-audio-files" type="file" multiple :accept="TRAINING_AUDIO_ACCEPT" :aria-label="t('musicLora.audioImport.files')" class="hidden" :disabled="disabled || busy || limit < 1" @change="selected" />
    <input ref="folderInput" name="training-audio-folder" type="file" multiple webkitdirectory :accept="TRAINING_AUDIO_ACCEPT" :aria-label="t('musicLora.audioImport.folder')" class="hidden" :disabled="disabled || busy || limit < 1" @change="selected" />
    <div class="flex flex-wrap gap-2">
      <button type="button" class="audio-import-button" :disabled="disabled || busy || limit < 1" @click="filesInput?.click()">{{ t('musicLora.audioImport.files') }}</button>
      <button type="button" class="audio-import-button" :disabled="disabled || busy || limit < 1" @click="folderInput?.click()">{{ t('musicLora.audioImport.folder') }}</button>
      <button v-if="busy" type="button" class="audio-import-button" @click="cancel">{{ t('musicLora.audioImport.cancel') }}</button>
    </div>
    <p class="text-xs text-text-dim">{{ t('musicLora.audioImport.capacity', { count: Math.max(0, limit) }) }}</p>
    <p v-if="busy" role="status" class="break-words text-sm text-text-dim">{{ reading ? t('musicLora.audioImport.reading') : t('musicLora.audioImport.progress', { current: processed + 1, total, file: filename }) }}</p>
    <div v-if="report" role="status" class="space-y-1 text-sm text-text-dim">
      <p>{{ t('musicLora.audioImport.added', { count: report.added }) }}</p>
      <p v-if="report.skipped">{{ t('musicLora.audioImport.skipped', { count: report.skipped }) }}</p>
      <p v-if="report.duplicates">{{ t('musicLora.audioImport.duplicates', { count: report.duplicates }) }}</p>
      <p v-if="report.limited">{{ t('musicLora.audioImport.limited', { count: report.limited }) }}</p>
      <p v-if="report.unreadable">{{ t('musicLora.audioImport.unreadable', { count: report.unreadable }) }}</p>
      <p v-if="report.truncated">{{ t('musicLora.audioImport.truncated') }}</p>
      <p v-if="report.cancelled">{{ t('musicLora.audioImport.cancelled') }}</p>
      <p v-else-if="report.stopped">{{ t('musicLora.audioImport.stopped') }}</p>
    </div>
    <div v-if="report?.failed.length" role="alert" class="space-y-1 text-sm text-status-queued">
      <p>{{ t('musicLora.audioImport.failed') }}</p>
      <ul class="list-inside list-disc break-words"><li v-for="file in report.failed" :key="file">{{ file }}</li></ul>
    </div>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.audio-import-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
