<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/videoScenes'
import { CHARACTER_SWAP_MAX_VIDEO_BYTES } from '../../api/characterSwaps'
import type { VideoSceneSource, VideoSceneReadiness, VideoScenePreview, VideoSceneCut } from '../../api/contracts'
import { ApiError } from '../../api/http'
import { createPollingLoop } from '../../composables/polling'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import { formatClock } from '../../composables/voicePace'
const emit = defineEmits<{ useCut: [cut: VideoSceneCut]; cutDeleted: [cut: VideoSceneCut] }>()
const { t, te } = useI18n()
const sources = ref<VideoSceneSource[]>([]), sourceId = ref(''), cuts = ref<VideoSceneCut[]>([])
const readiness = ref<VideoSceneReadiness | null>(null), preview = ref<VideoScenePreview | null>(null), player = ref<HTMLVideoElement | null>(null)
const busy = ref(false), error = ref(''), position = ref(0), seekSec = ref(0), inSec = ref(0), outSec = ref(8), name = ref(t('videoScenes.scene'))
const deleting = ref(false), savedScenes = ref<HTMLElement | null>(null), savedHeading = ref<HTMLElement | null>(null)
const source = computed(() => sources.value.find(value => value.id === sourceId.value))
const active = computed(() => cuts.value.some(value => value.status === 'queued' || value.status === 'cutting'))
const validRange = computed(() => source.value && Number.isFinite(inSec.value) && Number.isFinite(outSec.value) && inSec.value >= 0 && outSec.value > inSec.value && outSec.value <= source.value.duration_sec && outSec.value - inSec.value <= (readiness.value?.max_cut_seconds ?? 600))
const validSeek = computed(() => source.value && Number.isFinite(seekSec.value) && seekSec.value >= 0 && seekSec.value < source.value.duration_sec)
let alive = true, generation = 0, controller: AbortController | undefined
const loop = createPollingLoop(async ({ signal, isCurrent }) => {
  const id = sourceId.value, token = generation
  if (!active.value || busy.value) return false
  try { const result = await api.listSceneCuts(id, signal); if (alive && isCurrent() && token === generation && sourceId.value === id) { cuts.value = result.cuts; error.value = '' } }
  catch (value) { if (alive && isCurrent() && token === generation) error.value = errorText(value) }
  return active.value
}, 3000)
function stopPlayer() { if (player.value) { player.value.pause(); releasePlaybackIfCurrent(player.value) } }
function invalidate() { generation++; controller?.abort(); controller = undefined; loop.stop() }
function current(token: number, request: AbortController) { return alive && token === generation && !request.signal.aborted }
function errorText(value: unknown) { const key = value instanceof ApiError ? `videoScenes.errors.${value.message}` : ''; return t(key && te(key) ? key : 'videoScenes.errors.unknown') }
function schedule() { if (alive && active.value) loop.start(false) }
function saveCut(value: VideoSceneCut) { cuts.value = [value, ...cuts.value.filter(cut => cut.id !== value.id)] }
async function load() {
  if (busy.value) return
  invalidate(); const token = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  try {
    const [available, saved] = await Promise.all([api.sceneReadiness(request.signal), api.listSceneSources(request.signal)])
    if (!current(token, request)) return
    readiness.value = available; sources.value = saved.sources
    const id = sources.value.some(value => value.id === sourceId.value) ? sourceId.value : sources.value[0]?.id
    busy.value = false
    if (id) await activate(id)
  } catch (value) { if (current(token, request)) error.value = errorText(value) }
  finally { if (current(token, request)) { busy.value = false; schedule() } }
}
async function activate(id: string) {
  if (deleting.value) return
  invalidate(); stopPlayer(); sourceId.value = id; cuts.value = []; preview.value = null; position.value = 0; seekSec.value = 0; inSec.value = 0; outSec.value = Math.min(8, source.value?.duration_sec ?? 8)
  const token = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  try {
    const result = await api.listSceneCuts(id, request.signal)
    if (!current(token, request)) return
    cuts.value = result.cuts
    if (readiness.value?.ffmpeg_ready && !active.value) { const window = await api.previewSceneSource(id, { start_sec: 0 }, request.signal); if (current(token, request) && window.source_id === id) preview.value = window }
  } catch (value) { if (current(token, request)) error.value = errorText(value) }
  finally { if (current(token, request)) { busy.value = false; schedule() } }
}
function selectedSource(event: Event) { const target = event.target; if (!deleting.value && target instanceof HTMLSelectElement && sources.value.some(value => value.id === target.value)) void activate(target.value) }
async function importSource(video?: File) {
  if (busy.value || !readiness.value?.ffmpeg_ready || !video && !readiness.value.native_picker_available) return
  invalidate(); const token = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  try {
    const value = video ? await api.uploadSceneSource(video, request.signal) : await api.pickSceneSource(request.signal)
    if (!current(token, request)) return
    sources.value = [value, ...sources.value.filter(source => source.id !== value.id)]; busy.value = false; await activate(value.id)
  } catch (value) { if (current(token, request) && !(value instanceof ApiError && value.message === 'picker_cancelled')) error.value = errorText(value) }
  finally { if (current(token, request)) { busy.value = false; schedule() } }
}
function upload(event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const video = event.target.files?.[0]
  if (!video) return
  if (!/\.(avi|mkv|mp4|mov|webm)$/i.test(video.name)) { error.value = t('videoScenes.errors.unsupported_format'); return }
  if (video.size > (readiness.value?.max_upload_bytes ?? 2147483648)) { error.value = t('videoScenes.errors.upload_too_large'); return }
  void importSource(video)
}
async function seek() {
  if (busy.value || !validSeek.value || !source.value || !readiness.value?.ffmpeg_ready) return
  invalidate(); stopPlayer(); const id = sourceId.value, token = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  try { const value = await api.previewSceneSource(id, { start_sec: seekSec.value }, request.signal); if (current(token, request) && value.source_id === id) { preview.value = value; position.value = value.start_sec } }
  catch (value) { if (current(token, request)) error.value = errorText(value) }
  finally { if (current(token, request)) { busy.value = false; schedule() } }
}
function playbackTime() { if (player.value && preview.value) { position.value = Math.min(source.value?.duration_sec ?? Infinity, preview.value.start_sec + player.value.currentTime); seekSec.value = position.value } }
function playing() { if (player.value) claimPlayback(player.value) }
function paused() { if (player.value) releasePlaybackIfCurrent(player.value) }
async function create() {
  if (busy.value || active.value || !validRange.value || !readiness.value?.ffmpeg_ready || !name.value.trim() || name.value.trim().length > 120) return
  invalidate(); const id = sourceId.value, token = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  try { const value = await api.createSceneCut(id, { name: name.value.trim(), start_sec: inSec.value, end_sec: outSec.value }, request.signal); if (current(token, request) && value.source_id === id) saveCut(value) }
  catch (value) { if (current(token, request)) error.value = errorText(value) }
  finally { if (current(token, request)) { busy.value = false; schedule() } }
}
async function cancel(cut: VideoSceneCut) {
  if (busy.value || cut.status !== 'queued' && cut.status !== 'cutting') return
  invalidate(); const token = generation, request = new AbortController(); controller = request; busy.value = true; error.value = ''
  try { const value = await api.cancelSceneCut(cut.source_id, cut.id, request.signal); if (current(token, request) && value.source_id === sourceId.value && value.id === cut.id) saveCut(value) }
  catch (value) { if (current(token, request)) error.value = errorText(value) }
  finally { if (current(token, request)) { busy.value = false; schedule() } }
}
function deleteBlocked(cut: VideoSceneCut) { return cut.status === 'queued' || cut.status === 'cutting' || cut.error_code === 'worker_unresolved' }
function cutTime(seconds: number) {
  const milliseconds = Math.round(seconds * 1000), fraction = milliseconds % 1000
  return formatClock(Math.floor(milliseconds / 1000)) + (fraction ? `.${String(fraction).padStart(3, '0')}` : '')
}
async function remove(cut: VideoSceneCut, event: Event) {
  if (busy.value || deleteBlocked(cut) || sourceId.value !== cut.source_id || !cuts.value.some(value => value.id === cut.id && value.source_id === cut.source_id)) return
  if (!window.confirm(t('videoScenes.confirmDelete', { name: cut.name, start: cutTime(cut.start_sec), end: cutTime(cut.end_sec) }))) return
  if (busy.value || sourceId.value !== cut.source_id || !cuts.value.some(value => value.id === cut.id && value.source_id === cut.source_id && !deleteBlocked(value))) return
  const index = cuts.value.findIndex(value => value.id === cut.id), initiating = event.currentTarget
  const ownsFocus = initiating instanceof HTMLButtonElement && document.activeElement === initiating
  invalidate(); const token = generation, request = new AbortController(); controller = request; busy.value = true; deleting.value = true; error.value = ''
  let accepted = false
  try {
    const result = await api.deleteSceneCut(cut.source_id, cut.id, request.signal)
    if (!current(token, request) || sourceId.value !== cut.source_id) return
    if (result.source_id !== cut.source_id || result.deleted !== cut.id) throw new ApiError('cut_changed', 409)
    cuts.value = cuts.value.filter(value => value.id !== cut.id || value.source_id !== cut.source_id)
    accepted = true; emit('cutDeleted', cut)
  } catch (value) { if (current(token, request)) error.value = errorText(value) }
  finally {
    if (current(token, request)) {
      busy.value = false; deleting.value = false; schedule()
      if (ownsFocus) void nextTick(() => {
        if (!current(token, request) || document.activeElement !== initiating && document.activeElement !== document.body) return
        if (!accepted) { if (initiating instanceof HTMLElement && initiating.isConnected) initiating.focus(); return }
        const neighbor = cuts.value[Math.min(index, cuts.value.length - 1)]
        const row = [...savedScenes.value?.querySelectorAll<HTMLElement>('[data-scene-cut]') ?? []].find(value => value.dataset.sceneCut === neighbor?.id)
        const target = row?.querySelector<HTMLButtonElement>('[data-scene-delete]:not(:disabled)') ?? row?.querySelector<HTMLButtonElement>('button:not(:disabled)') ?? savedHeading.value
        target?.focus()
      })
    }
  }
}
function useCut(cut: VideoSceneCut) { if (!deleting.value && cut.status === 'ready' && cut.seconds >= 2 && cut.output_url && (cut.bytes ?? 0) <= CHARACTER_SWAP_MAX_VIDEO_BYTES) { stopPlayer(); emit('useCut', cut) } }
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; invalidate(); stopPlayer() })
</script>
<template>
  <section class="scene-cutter space-y-5" aria-labelledby="scene-cutter-heading" :aria-busy="busy">
    <header><h2 id="scene-cutter-heading" class="text-xl font-semibold">{{ t('videoScenes.title') }}</h2><p class="mt-2 text-sm text-text-dim">{{ t('videoScenes.intro') }}</p></header>
    <div class="scene-card space-y-3">
      <h3 class="font-semibold">{{ t('videoScenes.sourceStep') }}</h3>
      <label v-if="sources.length">{{ t('videoScenes.source') }}<select data-scene-source :value="sourceId" :disabled="deleting" @change="selectedSource"><option v-for="movie in sources" :key="movie.id" :value="movie.id">{{ movie.name }}</option></select></label><p v-else class="text-sm text-text-dim">{{ t('videoScenes.empty') }}</p>
      <button data-scene-pick type="button" :disabled="busy || !readiness?.native_picker_available || !readiness.ffmpeg_ready" @click="importSource()">{{ t('videoScenes.pick') }}</button>
      <p class="text-sm text-text-dim">{{ t('videoScenes.nativeHint') }}</p>
      <p v-if="readiness && !readiness.native_picker_available" class="text-sm text-text-dim">{{ t('videoScenes.nativeUnavailable') }}</p>
      <label>{{ t('videoScenes.upload') }}<input data-scene-upload type="file" accept=".avi,.mkv,.mp4,.mov,.webm,video/x-msvideo,video/x-matroska,video/mp4,video/quicktime,video/webm" :disabled="busy || !readiness?.ffmpeg_ready" @change="upload"></label>
      <p class="text-xs text-text-dim">{{ t('videoScenes.uploadHint') }}</p>
      <button type="button" :disabled="busy" @click="load">{{ t('videoScenes.refresh') }}</button>
    </div>
    <p v-if="readiness && !readiness.ffmpeg_ready" role="status">{{ t('videoScenes.ffmpeg') }}</p>
    <p v-if="busy" role="status">{{ t(deleting ? 'videoScenes.deleting' : 'videoScenes.working') }}</p><p v-if="error" role="alert" class="text-status-failed">{{ error }}</p>
    <div v-if="source" class="scene-card space-y-4">
      <h3 class="font-semibold">{{ t('videoScenes.trimStep') }}</h3>
      <p class="text-sm">{{ t('videoScenes.duration', { time: formatClock(source.duration_sec), width: source.width, height: source.height }) }}</p>
      <p v-if="!source.has_audio" class="text-xs text-text-dim">{{ t('videoScenes.noAudio') }}</p>
      <video v-if="preview" :key="preview.id" ref="player" data-scene-preview :src="preview.url" :aria-label="t('videoScenes.preview')" controls playsinline preload="metadata" class="aspect-video max-h-96 w-full rounded-lg bg-panel-2 object-contain" @timeupdate="playbackTime" @play="playing" @pause="paused" @ended="paused"></video>
      <p class="text-sm text-text-dim">{{ t('videoScenes.previewHint') }}</p>
      <label>{{ t('videoScenes.timeline') }}<input v-model.number="seekSec" type="range" min="0" :max="source.duration_sec" step="0.01" :disabled="busy || !readiness?.ffmpeg_ready" @change="seek"></label>
      <div class="flex flex-wrap items-end gap-3"><label class="min-w-0 flex-1">{{ t('videoScenes.position') }}<input v-model.number="seekSec" data-scene-position type="number" min="0" :max="source.duration_sec" step="0.001" :disabled="busy" @change="seek"></label><button data-scene-seek type="button" :disabled="busy || !validSeek || !readiness?.ffmpeg_ready" @click="seek">{{ t('videoScenes.seek') }}</button></div>
      <p class="text-sm">{{ t('videoScenes.playhead', { time: formatClock(position) }) }}</p>
      <form class="space-y-4" @submit.prevent="create">
        <div class="grid gap-4 sm:grid-cols-2"><label class="sm:col-span-2">{{ t('videoScenes.name') }}<input v-model="name" type="text" maxlength="120" required :disabled="busy"></label>
          <div class="space-y-2"><label>{{ t('videoScenes.in') }}<input v-model.number="inSec" data-scene-in type="number" min="0" :max="source.duration_sec" step="any" required :disabled="busy"></label><button data-scene-mark-in type="button" :disabled="busy || !preview" @click="inSec = position">{{ t('videoScenes.markIn') }}</button></div>
          <div class="space-y-2"><label>{{ t('videoScenes.out') }}<input v-model.number="outSec" data-scene-out type="number" min="0" :max="source.duration_sec" step="any" required :disabled="busy"></label><button data-scene-mark-out type="button" :disabled="busy || !preview" @click="outSec = position">{{ t('videoScenes.markOut') }}</button></div>
        </div>
        <p class="text-sm text-text-dim">{{ t('videoScenes.rangeHint') }}</p><p v-if="validRange" class="text-sm">{{ t('videoScenes.selection', { seconds: (outSec - inSec).toFixed(3) }) }}</p><p v-else role="status" class="text-sm text-status-failed">{{ t('videoScenes.invalidRange') }}</p>
        <button data-scene-create type="submit" class="primary" :disabled="busy || active || !validRange || !name.trim() || !readiness?.ffmpeg_ready">{{ t('videoScenes.create') }}</button>
      </form>
    </div>
    <section v-if="source" ref="savedScenes" class="space-y-3" :aria-label="t('videoScenes.saved')"><h3 id="scene-cutter-saved-heading" ref="savedHeading" tabindex="-1" class="font-semibold focus-visible:outline-2 focus-visible:outline-accent1">{{ t('videoScenes.savedStep') }}</h3><p v-if="!cuts.length" class="text-sm text-text-dim">{{ t('videoScenes.noCuts') }}</p>
      <ul class="grid gap-4 md:grid-cols-2"><li v-for="cut in cuts" :key="cut.id" :data-scene-cut="cut.id" class="scene-card space-y-3"><h4 class="break-words font-medium">{{ cut.name }}</h4><p role="status" class="text-sm">{{ t(`videoScenes.status.${cut.status}`) }} · {{ formatClock(cut.start_sec) }}–{{ formatClock(cut.end_sec) }}</p>
        <p v-if="cut.error_code" role="alert" class="text-sm text-status-failed">{{ t(te(`videoScenes.errors.${cut.error_code}`) ? `videoScenes.errors.${cut.error_code}` : 'videoScenes.errors.unknown') }}</p>
        <a v-if="cut.status === 'ready' && cut.output_url" :href="cut.output_url" data-scene-download download class="inline-flex min-h-11 items-center text-text underline">{{ t('videoScenes.download') }}</a>
        <button v-if="cut.status === 'queued' || cut.status === 'cutting'" data-scene-cancel type="button" :disabled="busy" @click="cancel(cut)">{{ t('videoScenes.cancel') }}</button>
        <button v-if="cut.status === 'ready' && cut.seconds >= 2 && cut.output_url" data-scene-use type="button" :disabled="deleting || (cut.bytes ?? 0) > CHARACTER_SWAP_MAX_VIDEO_BYTES" @click="useCut(cut)">{{ t('videoScenes.use') }}</button>
        <button :data-scene-delete="cut.id" type="button" :disabled="busy || deleteBlocked(cut)" :aria-label="t('videoScenes.deleteNamed', { name: cut.name, start: cutTime(cut.start_sec), end: cutTime(cut.end_sec) })" :aria-describedby="deleteBlocked(cut) ? `scene-${cut.id}-delete-hint` : undefined" class="text-status-failed" @click="remove(cut, $event)">{{ t('videoScenes.delete') }}</button>
        <p v-if="deleteBlocked(cut)" :id="`scene-${cut.id}-delete-hint`" class="text-xs text-text-dim">{{ t(cut.error_code === 'worker_unresolved' ? 'videoScenes.cleanupBeforeDelete' : 'videoScenes.cancelBeforeDelete') }}</p>
        <p v-if="cut.status === 'ready' && (cut.bytes ?? 0) > CHARACTER_SWAP_MAX_VIDEO_BYTES" class="text-xs text-text-dim">{{ t('videoScenes.swapTooLarge') }}</p>
      </li></ul><p class="text-xs text-text-dim">{{ t('videoScenes.swapHint') }}</p>
    </section><p class="text-xs text-text-dim">{{ t('videoScenes.background') }}</p>
  </section>
</template>
<style scoped>
.scene-cutter label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
.scene-cutter input:not([type=range]), .scene-cutter select { width: 100%; min-width: 0; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
.scene-cutter input[type=range] { min-height: 44px; width: 100%; }
.scene-cutter button { min-height: 44px; padding: .5rem .75rem; border-radius: .5rem; background: var(--color-panel-2); }
.scene-cutter button.primary { background: var(--color-accent1); color: white; }
.scene-cutter button:disabled { opacity: .45; cursor: not-allowed; }
.scene-cutter button:focus-visible, .scene-cutter input:focus-visible, .scene-cutter select:focus-visible, .scene-cutter a:focus-visible { outline: 2px solid var(--color-accent1); outline-offset: 2px; }
.scene-card { min-width: 0; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-panel); padding: 20px; }
</style>
