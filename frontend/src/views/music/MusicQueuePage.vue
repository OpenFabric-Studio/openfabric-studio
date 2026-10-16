<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import * as music from '../../api/musicQueue'
import { ApiError } from '../../api/http'
import { parseMusicQueuePreviewRequest } from '../../api/generated'
import type { MusicQueueBatch, MusicQueueCatalog, MusicQueueControlRequest, MusicQueueEngineSelection, MusicQueueItem, MusicQueueModelPlan, MusicQueuePreview, MusicQueuePreviewRequest, MusicQueueReadiness, MusicQueueSnapshot } from '../../api/generated'
import { createPollingLoop } from '../../composables/polling'
import { formatDuration } from '../../composables/formatDuration'
import { useDialogA11y } from '../../composables/useDialogA11y'
import { usePagination } from '../../composables/usePagination'
import AppIcon from '../../components/shared/AppIcon.vue'
import MusicAdapterSelect from '../../components/shared/MusicAdapterSelect.vue'
import TrackAudioVersions from '../../components/shared/TrackAudioVersions.vue'
import FavoriteTrackButton from '../../components/shared/FavoriteTrackButton.vue'
import StemsPanel from '../../components/shared/StemsPanel.vue'
import PaginationBar from '../../components/shared/PaginationBar.vue'

type Engine = MusicQueueEngineSelection['engine']
const { t } = useI18n()
const engines: Engine[] = ['ace_step', 'yue2', 'stable_audio3']
const catalog = ref<MusicQueueCatalog | null>(null), snapshot = ref<MusicQueueSnapshot | null>(null)
const title = ref(''), description = ref(''), lyrics = ref(''), instrumental = ref(false)
const duration = ref<number | string>(120), bpm = ref<number | string>(''), musicalKey = ref(''), language = ref('')
const selections = ref<MusicQueueEngineSelection[]>([])
const preview = ref<MusicQueuePreview | null>(null), previewLoading = ref(false), busy = ref(false)
const catalogError = ref(''), catalogLoading = ref(false), observationError = ref(''), previewError = ref(''), actionError = ref('')
const arValid = ref(true), narValid = ref(true), stableValid = ref(true)
const activeTab = ref<'create' | 'results'>('create'), showInputs = ref(false)
const dialog = ref<HTMLElement | null>(null), queueHeading = ref<HTMLElement | null>(null)
const descriptionInput = ref<HTMLTextAreaElement | null>(null)
let alive = true, previewGeneration = 0, actionGeneration = 0
let previewController: AbortController | undefined, actionController: AbortController | undefined
let previewTimer: ReturnType<typeof setTimeout> | undefined
let submissionKey: string | null = null
const catalogController = new AbortController()
useDialogA11y(dialog, () => showInputs.value, () => { showInputs.value = false })

function label(engine: Engine): string { return t(`musicQueue.models.${engine}`) }
function selectionFor(engine: Engine) { return selections.value.find(selection => selection.engine === engine) }
function readinessFor(engine: Engine) { return catalog.value?.engines.find(model => model.engine === engine) }
function incompatible(engine: Engine): boolean { return engine === 'stable_audio3' && !instrumental.value || engine === 'yue2' && instrumental.value }
function modelHint(engine: Engine): string {
  if (engine === 'stable_audio3') return t('musicQueue.instrumentalOnly')
  if (engine === 'yue2') return t('musicQueue.yueLyrics')
  return t(`musicQueue.hints.${engine}`)
}
function readinessText(model: MusicQueueReadiness | undefined): string {
  if (!model) return t('musicQueue.readiness.unknown')
  if (!model.supported) return t('musicQueue.readiness.unsupported')
  if (!model.installed) return t('musicQueue.readiness.runtime')
  if (!model.weights_ready) return t('musicQueue.readiness.weights')
  return t(model.ready ? 'musicQueue.readiness.ready' : 'musicQueue.readiness.unavailable')
}
function toggleModel(engine: Engine) {
  if (selectionFor(engine)) selections.value = selections.value.filter(selection => selection.engine !== engine)
  else if (readinessFor(engine)?.ready && !incompatible(engine)) selections.value.push({ engine, variations: 1, variant: 'small-music', precision: 'q8_0', adapter: null, ar_adapter: null, nar_adapter: null })
}
function setInstrumental(value: boolean) {
  instrumental.value = value
  selections.value = selections.value.filter(selection => !incompatible(selection.engine))
}
const adaptersValid = computed(() => (!selectionFor('yue2') || arValid.value && narValid.value) && (!selectionFor('stable_audio3') || stableValid.value))
const total = computed(() => selections.value.reduce((sum, selection) => sum + (typeof selection.variations === 'number' && Number.isInteger(selection.variations) && selection.variations >= 1 && selection.variations <= 8 ? selection.variations : 0), 0))
const request = computed<MusicQueuePreviewRequest | null>(() => {
  if (!description.value.trim() || !adaptersValid.value || bpm.value !== '' && typeof bpm.value !== 'number') return null
  try {
    return parseMusicQueuePreviewRequest({ brief: { title: title.value, description: description.value, lyrics: instrumental.value ? '' : lyrics.value, instrumental: instrumental.value, duration_seconds: duration.value, bpm: bpm.value === '' ? null : bpm.value, key: musicalKey.value, language: language.value }, selections: selections.value.map(selection => ({ ...selection })) })
  } catch { return null }
})
const canEnqueue = computed(() => !!request.value && !!preview.value?.can_enqueue && !previewLoading.value && !busy.value && snapshot.value !== null)
function errorText(code: string | undefined, fallback = 'musicQueue.errors.action'): string {
  switch (code) {
    case 'ace_lora_selection_mismatch':
    case 'ace_lora_status_unavailable':
    case 'ace_lora_model_inactive': return t(`aceGen.loraErrors.${code}`)
    case 'music_queue_revision_conflict': return t('musicQueue.errors.stale')
    case 'music_queue_preview_changed': return t('musicQueue.errors.planChanged')
    case 'music_queue_yue_lyrics_required': return t('musicQueue.errors.lyrics')
    case 'music_queue_yue_vocals_required': return t('musicQueue.yueLyrics')
    case 'stable_audio3_instrumental_only': return t('musicQueue.instrumentalOnly')
    case 'music_queue_prompt_too_long': return t('musicQueue.errors.prompt')
    case 'music_queue_capacity_exceeded': return t('musicQueue.errors.limit')
    case 'music_queue_engine_not_installed': return t('musicQueue.readiness.runtime')
    case 'music_queue_weights_missing':
    case 'local_music_weights_missing': return t('musicQueue.readiness.weights')
    case 'music_queue_runtime_unsupported':
    case 'local_music_runtime_unsupported': return t('musicQueue.readiness.unsupported')
    case 'music_lora_adapter_not_found': return t('musicLora.errors.missing')
    case 'music_lora_adapter_changed': return t('musicLora.errors.changed')
    case 'music_lora_runtime_unsupported': return t('musicLora.errors.runtime')
    case 'music_lora_target_mismatch':
    case 'music_lora_format_invalid': return t('musicLora.errors.format')
    case 'music_queue_cleanup_failed':
    case 'music_queue_engine_cleanup_required':
    case 'music_queue_child_unavailable': return t('musicQueue.errors.cleanup')
    case 'music_queue_submission_interrupted':
    case 'music_queue_child_missing': return t('musicQueue.errors.reconcile')
    case 'music_queue_busy':
    case 'native_model_busy':
    case 'module_setup_busy': return t('musicQueue.errors.busy')
    case 'music_queue_engine_transition': return t('musicQueue.errors.transition')
    case 'music_queue_engine_not_owned':
    case 'music_queue_runtime_unverified': return t('musicQueue.errors.unverified')
    case 'music_queue_interrupted': return t('musicQueue.errors.interrupted')
    case 'music_queue_plan_invalid':
    case 'music_queue_plan_unavailable': return t('musicQueue.errors.incompatible')
    case 'music_queue_engine_start_failed':
    case 'music_queue_engine_not_ready': return t('musicQueue.errors.start')
    case 'music_queue_save_retry_unavailable': return t('musicQueue.errors.saveUnavailable')
    case 'save_failed':
    case 'local_music_save_failed': return t('musicQueue.errors.save')
    default: return t(fallback)
  }
}
function applySnapshot(value: MusicQueueSnapshot) {
  if (!snapshot.value || value.revision >= snapshot.value.revision) snapshot.value = value
}
const poll = createPollingLoop(async context => {
  try {
    const value = await music.getMusicQueue(context.signal)
    if (!alive || !context.isCurrent()) return false
    applySnapshot(value); observationError.value = ''
  } catch { if (alive && context.isCurrent()) observationError.value = t('musicQueue.errors.observe') }
  return alive
}, 2500)
async function loadCatalog() {
  if (catalogLoading.value || !alive) return
  catalogError.value = ''; catalogLoading.value = true
  try { const value = await music.musicQueueCatalog(catalogController.signal); if (alive) catalog.value = value }
  catch { if (alive) catalogError.value = t('musicQueue.errors.catalog') }
  finally { if (alive) catalogLoading.value = false }
}
watch([request, catalog], ([value]) => {
  const token = ++previewGeneration
  clearTimeout(previewTimer); previewController?.abort(); previewController = undefined
  showInputs.value = false; preview.value = null; previewError.value = ''; previewLoading.value = value !== null; submissionKey = null
  if (!value) return
  previewTimer = setTimeout(async () => {
    const controller = new AbortController(); previewController = controller
    try {
      const result = await music.previewMusicQueue(value, controller.signal)
      if (alive && token === previewGeneration) preview.value = result
    } catch (cause) { if (alive && token === previewGeneration) previewError.value = errorText(cause instanceof ApiError ? cause.message : undefined, 'musicQueue.errors.preview') }
    finally { if (alive && token === previewGeneration) { previewLoading.value = false; previewController = undefined } }
  }, 300)
}, { deep: true, flush: 'sync' })

async function perform(work: (signal: AbortSignal) => Promise<MusicQueueSnapshot>): Promise<boolean> {
  if (!alive || busy.value || !snapshot.value) return false
  poll.stop(); const token = ++actionGeneration, controller = new AbortController(); actionController = controller
  const current = () => alive && token === actionGeneration && !controller.signal.aborted
  busy.value = true; actionError.value = ''; let succeeded = false
  try { const result = await work(controller.signal); if (current()) { applySnapshot(result); succeeded = true } }
  catch (cause) { if (current()) actionError.value = errorText(cause instanceof ApiError ? cause.message : undefined) }
  finally { if (current()) { busy.value = false; actionController = undefined; poll.start(!succeeded) } }
  return succeeded
}
async function enqueue() {
  const plan = preview.value, revision = snapshot.value?.revision
  if (!canEnqueue.value || !plan || revision === undefined) return
  submissionKey ??= crypto.randomUUID()
  const saved = await perform(signal => music.enqueueMusicQueue({ brief: plan.brief, selections: plan.selections, preview_fingerprint: plan.fingerprint, revision, submission_key: submissionKey ?? '' }, signal))
  if (saved && alive) { submissionKey = null; await nextTick(); if (alive) queueHeading.value?.focus() }
}
function control(action: MusicQueueControlRequest['action']) {
  const revision = snapshot.value?.revision
  if (revision !== undefined) void perform(signal => music.controlMusicQueue({ revision, action }, signal))
}
const batches = computed(() => snapshot.value?.batches ?? [])
const items = computed(() => batches.value.flatMap(batch => batch.items))
const supersededItemIds = computed(() => new Set(items.value.flatMap(item => item.retry_of_item_id ? [item.retry_of_item_id] : [])))
function currentItems(batch: MusicQueueBatch): MusicQueueItem[] { return batch.items.filter(item => !supersededItemIds.value.has(item.id)) }
const queueBatches = computed(() => batches.value.filter(batch => currentItems(batch).some(item => item.status !== 'done' && item.status !== 'cancelled')))
const playingTracks = ref<ReadonlySet<number>>(new Set()), processingTracks = ref<ReadonlySet<number>>(new Set())
function noteActivity(kind: 'playing' | 'processing', id: number, active: boolean) {
  const target = kind === 'playing' ? playingTracks : processingTracks, next = new Set(target.value)
  if (active) next.add(id); else next.delete(id)
  target.value = next
}
const { pageItems: resultPageItems, barProps: resultBarProps, setPage: setResultPage, setPageSize: setResultPageSize } = usePagination(() => items.value.flatMap(item => (item.tracks ?? []).map(track => ({ item, track }))))
const visibleTrackIds = computed(() => new Set([...resultPageItems.value.map(row => row.track.id), ...playingTracks.value, ...processingTracks.value]))
function visibleTracks(item: MusicQueueItem) { return (item.tracks ?? []).filter(track => visibleTrackIds.value.has(track.id)) }
function hasVisibleResults(batch: MusicQueueBatch, engine?: Engine): boolean { return batch.items.some(item => (engine === undefined || item.engine === engine) && visibleTracks(item).length > 0) }
const savedCount = computed(() => items.value.reduce((sum, item) => sum + (item.tracks?.length ?? 0), 0))
const activeItem = computed(() => items.value.find(item => item.id === snapshot.value?.active_item_id))
const running = computed(() => snapshot.value?.status === 'running' || snapshot.value?.status === 'pausing')
const pendingCount = computed(() => items.value.filter(item => !supersededItemIds.value.has(item.id) && (item.status ?? 'queued') === 'queued').length)
function unstarted(batch: MusicQueueBatch): boolean { return batch.items.length > 0 && batch.items.every(item => (item.status ?? 'queued') === 'queued' && !item.child_job_id && (item.attempt ?? 1) === 1) }
const reorderable = computed(() => batches.value.filter(unstarted))
function move(batch: MusicQueueBatch, direction: -1 | 1) {
  const revision = snapshot.value?.revision, ids = reorderable.value.map(value => value.id), index = ids.indexOf(batch.id), other = ids[index + direction]
  if (revision === undefined || index < 0 || !other) return
  ids[index] = other; ids[index + direction] = batch.id
  void perform(signal => music.reorderMusicQueue({ revision, batch_ids: ids }, signal))
}
function cancel(batch: MusicQueueBatch) {
  const revision = snapshot.value?.revision
  if (revision !== undefined) void perform(signal => music.cancelMusicQueueBatch({ revision, batch_id: batch.id }, signal))
}
function retry(item: MusicQueueItem, mode: 'inference' | 'save') {
  const revision = snapshot.value?.revision
  if (revision !== undefined) void perform(signal => music.retryMusicQueue({ revision, item_id: item.id, mode }, signal))
}
function canRetrySave(item: MusicQueueItem): boolean { return !!item.child_job_id && (item.error_code === 'save_failed' || item.error_code === 'local_music_save_failed') }
function completed(batch: MusicQueueBatch): number { return currentItems(batch).filter(item => item.status === 'done').length }
function batchTitle(batch: MusicQueueBatch): string { return batch.brief.title || t('musicQueue.untitled') }
function modelPlan(engine: Engine): MusicQueueModelPlan | undefined { return preview.value?.models.find(model => model.selection.engine === engine) }
function changeVariant(selection: MusicQueueEngineSelection) { selection.adapter = null; stableValid.value = true }
function newBrief() {
  title.value = ''; description.value = ''; lyrics.value = ''; bpm.value = ''; musicalKey.value = ''; language.value = ''
  activeTab.value = 'create'; void nextTick(() => { if (alive) descriptionInput.value?.focus() })
}
function tabKey(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault(); activeTab.value = event.key === 'Home' ? 'create' : event.key === 'End' ? 'results' : activeTab.value === 'create' ? 'results' : 'create'
  document.getElementById(`music-queue-${activeTab.value}-tab`)?.focus()
}
watch(activeTab, tab => { if (tab === 'create') { playingTracks.value = new Set(); processingTracks.value = new Set() } })
onMounted(() => { void loadCatalog(); poll.start() })
onBeforeUnmount(() => { alive = false; ++previewGeneration; ++actionGeneration; clearTimeout(previewTimer); previewController?.abort(); actionController?.abort(); catalogController.abort(); poll.stop() })
</script>

<template>
  <section class="min-w-0 space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div role="tablist" :aria-label="t('musicQueue.workspace')" class="flex gap-2" @keydown="tabKey">
        <button id="music-queue-create-tab" type="button" role="tab" aria-controls="music-queue-create" :aria-selected="activeTab === 'create'" :tabindex="activeTab === 'create' ? 0 : -1" class="mq-button" :class="activeTab === 'create' ? 'border-accent2 text-accent2' : 'text-text-dim'" @click="activeTab = 'create'">{{ t('musicQueue.create') }}</button>
        <button id="music-queue-results-tab" type="button" role="tab" aria-controls="music-queue-results" :aria-label="t('musicQueue.results')" :aria-selected="activeTab === 'results'" :tabindex="activeTab === 'results' ? 0 : -1" class="mq-button" :class="activeTab === 'results' ? 'border-accent2 text-accent2' : 'text-text-dim'" @click="activeTab = 'results'">{{ t('musicQueue.results') }} <span v-if="savedCount" aria-hidden="true" class="ml-2 rounded bg-panel-2 px-2">{{ savedCount }}</span><span class="sr-only">{{ t('musicQueue.savedCount', { count: savedCount }) }}</span></button>
      </div>
      <p v-if="snapshot" role="status" class="text-sm text-text-dim">{{ t(`musicQueue.queueStates.${snapshot.status}`) }}<span v-if="activeItem"> · {{ label(activeItem.engine) }} · {{ t(`musicQueue.itemStates.${activeItem.status ?? 'queued'}`) }}</span></p>
    </div>
    <p v-if="observationError" role="alert" class="text-sm text-status-failed">{{ observationError }}</p>
    <p v-if="actionError" role="alert" class="text-sm text-status-failed">{{ actionError }}</p>
    <p v-if="snapshot?.error_code" role="alert" class="text-sm text-status-failed">{{ errorText(snapshot.error_code, 'musicQueue.errors.paused') }}</p>
    <p v-if="snapshot?.history_incomplete" class="text-sm text-text-dim">{{ t('musicQueue.olderHistory') }} <RouterLink to="/editor/new" class="mq-text-button">{{ t('musicQueue.openEditor') }}</RouterLink> {{ t('musicQueue.olderLibraryHint') }}</p>

    <div v-show="activeTab === 'create'" id="music-queue-create" role="tabpanel" aria-labelledby="music-queue-create-tab" class="space-y-6">
      <div class="grid items-start gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(19rem,1fr)]">
        <div class="min-w-0 overflow-hidden rounded-2xl border border-border bg-panel">
          <form id="music-queue-brief" class="space-y-5 p-5 sm:p-6" @submit.prevent="enqueue">
            <h2 class="text-lg font-semibold text-text">{{ t('musicQueue.brief') }}</h2>
            <label class="mq-label">{{ t('musicQueue.title') }}<input v-model="title" name="title" maxlength="120" class="mq-input" /></label>
            <label class="mq-label">{{ t('musicQueue.description') }}<textarea ref="descriptionInput" v-model="description" name="description" rows="4" maxlength="3500" required class="mq-input resize-y" :placeholder="t('musicQueue.descriptionPlaceholder')" /></label>
            <div class="grid gap-4 sm:grid-cols-2">
              <fieldset><legend class="mb-2 text-sm text-text-dim">{{ t('musicQueue.vocals') }}</legend><div class="flex gap-2"><button type="button" :aria-pressed="!instrumental" class="mq-button flex-1" :class="!instrumental ? 'border-accent2 bg-accent1/15 text-text' : 'text-text-dim'" @click="setInstrumental(false)">{{ t('musicQueue.withVocals') }}</button><button type="button" :aria-pressed="instrumental" class="mq-button flex-1" :class="instrumental ? 'border-accent2 bg-accent1/15 text-text' : 'text-text-dim'" @click="setInstrumental(true)">{{ t('musicQueue.instrumental') }}</button></div></fieldset>
              <label class="mq-label">{{ t('musicQueue.duration') }}<input v-model.number="duration" name="duration_seconds" type="number" min="10" max="600" step="1" required class="mq-input" /></label>
            </div>
            <details v-show="!instrumental" class="rounded-xl border border-border p-3"><summary class="mq-summary">{{ t('musicQueue.lyrics') }} <span class="text-xs text-text-dim">{{ t('musicQueue.lyricsHint') }}</span></summary><label class="mq-label mt-3">{{ t('musicQueue.yourLyrics') }}<textarea v-model="lyrics" name="lyrics" rows="6" maxlength="20000" class="mq-input resize-y" /></label></details>
            <details class="border-t border-border pt-3"><summary class="mq-summary">{{ t('musicQueue.direction') }}</summary><div class="mt-3 grid gap-3 sm:grid-cols-3"><label class="mq-label">{{ t('musicQueue.tempo') }}<input v-model.number="bpm" name="bpm" type="number" min="20" max="300" step="any" class="mq-input" /></label><label class="mq-label">{{ t('musicQueue.key') }}<input v-model="musicalKey" name="key" maxlength="80" class="mq-input" /></label><label class="mq-label">{{ t('musicQueue.language') }}<input v-model="language" name="language" maxlength="80" class="mq-input" /></label></div></details>
          </form>
          <section aria-labelledby="music-queue-models" class="space-y-4 border-t border-border bg-panel-2/30 p-5 sm:p-6">
            <div class="flex flex-wrap items-center justify-between gap-3"><h2 id="music-queue-models" class="font-semibold text-text">{{ t('musicQueue.chooseModels') }}</h2><button type="button" :disabled="catalogLoading" class="mq-text-button" @click="loadCatalog">{{ t('musicQueue.refreshSetup') }}</button></div>
            <p v-if="catalogError" role="alert" class="text-sm text-status-failed">{{ catalogError }}</p>
            <div class="grid gap-3 sm:grid-cols-2">
              <div v-for="engine in engines" :key="engine" class="min-w-0 rounded-xl border p-3" :class="selectionFor(engine) ? 'border-accent2 bg-accent1/10' : 'border-border bg-panel'">
                <label class="flex min-h-11 cursor-pointer items-start gap-3"><input type="checkbox" :name="`model_${engine}`" :checked="!!selectionFor(engine)" :disabled="!selectionFor(engine) && (!readinessFor(engine)?.ready || incompatible(engine))" :aria-describedby="`queue-model-${engine}-hint`" class="mt-1 h-4 w-4 shrink-0 accent-accent2 focus-visible:outline-2 focus-visible:outline-accent2" @change="toggleModel(engine)" /><span class="min-w-0"><span class="block text-sm font-medium text-text">{{ label(engine) }} <span v-if="selectionFor(engine)" class="ml-1 text-xs text-accent2">#{{ selections.findIndex(selection => selection.engine === engine) + 1 }}</span></span><span :id="`queue-model-${engine}-hint`" class="mt-1 block text-xs leading-relaxed text-text-dim">{{ modelHint(engine) }}</span><span class="mt-2 block text-xs text-text-dim">{{ readinessText(readinessFor(engine)) }}</span></span></label>
                <RouterLink :to="`/settings#module-${engine}`" class="mq-text-button mt-1">{{ t('musicQueue.setup') }}</RouterLink>
              </div>
            </div>
            <p class="text-xs leading-relaxed text-text-dim">{{ t('musicQueue.orderHint') }}</p>
          </section>
        </div>

        <aside aria-labelledby="music-queue-plan" class="min-w-0 space-y-5 rounded-2xl border border-border bg-panel p-5 sm:p-6">
          <h2 id="music-queue-plan" class="text-lg font-semibold text-text">{{ t('musicQueue.plan') }}</h2>
          <p class="flex items-baseline gap-3"><strong data-variation-total class="text-4xl font-semibold tabular-nums text-text">{{ total }}</strong><span class="text-sm text-text-dim">{{ t('musicQueue.newVariations') }}</span></p>
          <p class="text-xs text-text-dim">{{ t('musicQueue.seedHint') }}</p>
          <p v-if="!selections.length" class="rounded-lg border border-dashed border-border p-4 text-sm text-text-dim">{{ t('musicQueue.emptyPlan') }}</p>
          <ol :aria-label="t('musicQueue.modelOrder')" class="space-y-4">
            <li v-for="(selection, index) in selections" :key="selection.engine" class="space-y-3 border-t border-border pt-4">
              <div class="flex items-start justify-between gap-3"><h3 class="text-sm font-medium text-text"><span class="mr-2 text-text-dim">{{ index + 1 }}.</span>{{ label(selection.engine) }}</h3><label class="mq-label w-24 shrink-0 text-xs">{{ t('musicQueue.variations') }}<input v-model.number="selection.variations" :name="`variations_${selection.engine}`" :aria-label="t('musicQueue.variationLabel', { model: label(selection.engine) })" type="number" min="1" max="8" step="1" required class="mq-input" /></label></div>
              <template v-if="modelPlan(selection.engine)"><p class="text-sm text-text-dim">{{ modelPlan(selection.engine)?.duration_mode === 'model_led' ? t('musicQueue.modelLed') : t('musicQueue.target', { duration: formatDuration(modelPlan(selection.engine)?.effective_duration_seconds ?? 0) }) }}</p><p v-for="adjustment in modelPlan(selection.engine)?.adjustments ?? []" :key="`${adjustment.field}-${adjustment.code}`" class="text-xs text-status-queued">{{ t('musicQueue.adjustment', { requested: adjustment.requested, effective: adjustment.effective }) }}</p><p v-if="modelPlan(selection.engine)?.readiness.ready === false" role="alert" class="text-sm text-status-failed">{{ readinessText(modelPlan(selection.engine)?.readiness) }}</p><p v-if="modelPlan(selection.engine)?.error_code" role="alert" class="text-sm text-status-failed">{{ errorText(modelPlan(selection.engine)?.error_code, 'musicQueue.errors.incompatible') }}</p></template>
              <p v-if="selection.engine === 'stable_audio3'" class="text-xs leading-relaxed text-text-dim">{{ t('musicQueue.oneShot') }}</p>
              <details><summary class="mq-summary text-xs">{{ t('musicQueue.modelSettings') }}</summary><div class="mt-3 space-y-3">
                <template v-if="selection.engine === 'yue2'"><label class="mq-label">{{ t('musicQueue.precision') }}<select v-model="selection.precision" name="precision" class="mq-input"><option value="q8_0">Q8</option><option value="q4_0">Q4</option></select></label><h4 class="text-xs font-medium text-text">{{ t('musicQueue.arAdapter') }}</h4><MusicAdapterSelect v-model="selection.ar_adapter" engine="yue2" target="ar" @validity="arValid = $event" /><h4 class="text-xs font-medium text-text">{{ t('musicQueue.narAdapter') }}</h4><MusicAdapterSelect v-model="selection.nar_adapter" engine="yue2" target="nar" @validity="narValid = $event" /></template>
                <template v-else-if="selection.engine === 'stable_audio3'"><label class="mq-label">{{ t('musicQueue.variant') }}<select v-model="selection.variant" name="variant" class="mq-input" @change="changeVariant(selection)"><option value="small-music">{{ t('localMusic.small') }}</option><option value="medium">{{ t('localMusic.medium') }}</option></select></label><MusicAdapterSelect v-model="selection.adapter" engine="stable_audio3" :target="selection.variant ?? 'small-music'" @validity="stableValid = $event" /></template>
                <p v-else class="text-xs text-text-dim">{{ t(selection.engine === 'ace_step' ? 'musicQueue.aceBase' : 'musicQueue.noAdapter') }}</p>
                <RouterLink v-if="selection.engine === 'yue2' || selection.engine === 'stable_audio3'" to="/music/lora" class="mq-text-button">{{ t('musicLora.manage') }}</RouterLink>
              </div></details>
            </li>
          </ol>
          <p v-if="previewLoading" role="status" class="text-sm text-text-dim">{{ t('musicQueue.previewing') }}</p>
          <p v-if="previewError" role="alert" class="text-sm text-status-failed">{{ previewError }}</p>
          <p v-if="selections.length && !request" class="text-xs text-status-queued">{{ t('musicQueue.invalidBrief') }}</p>
          <p class="border-t border-border pt-4 text-xs leading-relaxed text-text-dim">{{ t('musicQueue.executionHint') }}</p>
          <button type="button" :disabled="!preview || previewLoading" class="mq-text-button w-full justify-between" @click="showInputs = true">{{ t('musicQueue.adaptedInputs') }}<AppIcon name="expand" /></button>
          <button type="submit" form="music-queue-brief" class="mq-button accent-gradient w-full text-white" :disabled="!canEnqueue">{{ t(total === 1 ? 'musicQueue.enqueueOne' : 'musicQueue.enqueue', { count: total }) }}</button>
          <p class="text-center text-xs leading-relaxed text-text-dim">{{ t('musicQueue.enqueueHint') }}</p>
        </aside>
      </div>

      <section aria-labelledby="music-queue-heading" class="space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3"><h2 id="music-queue-heading" ref="queueHeading" tabindex="-1" class="rounded text-lg font-semibold text-text focus-visible:outline-2 focus-visible:outline-accent2">{{ t('musicQueue.queue') }}</h2><div class="flex flex-wrap gap-2"><button v-if="batches.length" type="button" class="mq-button" @click="newBrief">{{ t('musicQueue.newBrief') }}</button><button v-if="snapshot?.status === 'blocked'" type="button" class="mq-button" :disabled="busy" @click="control('cancel_active')">{{ t('musicQueue.retryCleanup') }}</button><template v-if="running"><button type="button" class="mq-button" :disabled="busy || snapshot?.status === 'pausing'" @click="control('pause')">{{ t(snapshot?.status === 'pausing' ? 'musicQueue.pausing' : 'musicQueue.pause') }}</button><button type="button" class="mq-button text-status-failed" :disabled="busy || !activeItem" @click="control('cancel_active')">{{ t('musicQueue.cancelActive') }}</button></template><button v-else type="button" class="mq-button border-accent2 text-accent2" :disabled="busy || !snapshot || !pendingCount || snapshot.status === 'blocked'" @click="control('start')">{{ t(snapshot?.status === 'paused' ? 'musicQueue.resume' : 'musicQueue.start') }}</button></div></div>
        <p v-if="!snapshot" role="status" class="text-sm text-text-dim">{{ t('musicQueue.loading') }}</p>
        <p v-else-if="!queueBatches.length" class="rounded-2xl border border-dashed border-border p-7 text-sm text-text-dim">{{ t('musicQueue.emptyQueue') }}</p>
        <article v-for="batch in queueBatches" :key="batch.id" data-queue-batch class="space-y-4 rounded-2xl border border-border bg-panel p-4 sm:p-5">
          <div class="flex flex-wrap items-start justify-between gap-3"><div class="min-w-0"><h3 class="break-words font-medium text-text">{{ batchTitle(batch) }}</h3><p class="mt-2 line-clamp-2 whitespace-pre-wrap break-words text-sm text-text-dim">{{ batch.brief.description }}</p><p class="mt-3 text-xs text-text-dim">{{ batch.selections.map(selection => label(selection.engine)).join(' → ') }} · {{ t('musicQueue.completed', { completed: completed(batch), total: currentItems(batch).length }) }}</p></div><div class="flex flex-wrap gap-2"><template v-if="unstarted(batch)"><button type="button" class="mq-button" :aria-label="t('musicQueue.moveEarlier', { title: batchTitle(batch) })" :disabled="busy || reorderable[0]?.id === batch.id" @click="move(batch, -1)">↑</button><button type="button" class="mq-button" :aria-label="t('musicQueue.moveLater', { title: batchTitle(batch) })" :disabled="busy || reorderable.at(-1)?.id === batch.id" @click="move(batch, 1)">↓</button></template><button v-if="currentItems(batch).some(item => (item.status ?? 'queued') === 'queued')" type="button" class="mq-button" :aria-label="t('musicQueue.cancelBrief', { title: batchTitle(batch) })" :disabled="busy || batch.items.some(item => item.id === activeItem?.id)" @click="cancel(batch)">{{ t('musicQueue.cancelPending') }}</button></div></div>
          <ul class="flex flex-wrap gap-2"><li v-for="item in batch.items" :key="item.id" class="rounded-lg border border-border bg-panel-2 px-3 py-2 text-xs text-text-dim"><span>{{ label(item.engine) }} · {{ t('musicQueue.take', { index: item.variation_index }) }} · {{ t(`musicQueue.itemStates.${item.status ?? 'queued'}`) }}</span><span v-if="supersededItemIds.has(item.id)" class="ml-2 text-text-dim">{{ t('musicQueue.previousAttempt') }}</span><span v-if="(item.attempt ?? 1) > 1"> · {{ t('musicQueue.attempt', { count: item.attempt }) }}</span><template v-if="item.status === 'failed' || item.status === 'interrupted' || item.status === 'blocked'"><p class="mt-2 max-w-sm text-status-failed">{{ errorText(item.error_code, 'musicQueue.errors.failed') }}</p><div v-if="!supersededItemIds.has(item.id)" class="mt-1 flex flex-wrap gap-3"><button type="button" class="mq-text-button" :disabled="busy" @click="retry(item, 'inference')">{{ t('musicQueue.retryInference') }}</button><button v-if="canRetrySave(item)" type="button" class="mq-text-button" :disabled="busy" @click="retry(item, 'save')">{{ t('musicQueue.retrySave') }}</button></div></template></li></ul>
        </article>
      </section>
    </div>

    <section v-if="activeTab === 'results'" id="music-queue-results" role="tabpanel" aria-labelledby="music-queue-results-tab" class="space-y-5">
      <div><h2 class="text-lg font-semibold text-text">{{ t('musicQueue.resultsHeading') }}</h2><p class="mt-2 text-sm text-text-dim">{{ t('musicQueue.resultsHint') }}</p></div>
      <p v-if="!savedCount" class="rounded-xl border border-dashed border-border p-6 text-sm text-text-dim">{{ t('musicQueue.emptyResults') }}</p>
      <PaginationBar v-bind="resultBarProps" @update:page="setResultPage" @update:page-size="setResultPageSize" />
      <template v-for="batch in batches" :key="batch.id"><section v-if="hasVisibleResults(batch)" class="space-y-4 rounded-2xl border border-border bg-panel p-5"><h3 class="font-semibold text-text">{{ batchTitle(batch) }}</h3><details data-attempt-history class="rounded-lg border border-border p-3"><summary class="mq-summary">{{ t('musicQueue.attemptHistory') }}</summary><ol class="mt-3 space-y-2 text-xs text-text-dim"><li v-for="item in batch.items" :key="item.id">{{ label(item.engine) }} · {{ t('musicQueue.take', { index: item.variation_index }) }} · {{ t('musicQueue.attempt', { count: item.attempt ?? 1 }) }} · {{ t(`musicQueue.itemStates.${item.status ?? 'queued'}`) }}<span v-if="supersededItemIds.has(item.id)"> · {{ t('musicQueue.previousAttempt') }}</span></li></ol></details><template v-for="selection in batch.selections" :key="selection.engine"><div v-if="hasVisibleResults(batch, selection.engine)" class="space-y-4"><h4 class="text-sm font-medium text-accent2">{{ label(selection.engine) }}</h4><template v-for="item in batch.items.filter(item => item.engine === selection.engine)" :key="item.id"><article v-for="track in visibleTracks(item)" :key="track.id" class="space-y-3 rounded-xl border border-border bg-panel-2 p-4"><div class="flex flex-wrap items-center justify-between gap-3"><div><h5 class="text-sm font-medium text-text">{{ track.title || batchTitle(batch) }}</h5><p class="mt-1 text-xs text-text-dim">{{ t('musicQueue.take', { index: item.variation_index }) }} · {{ t('musicQueue.attempt', { count: item.attempt ?? 1 }) }} · {{ t('musicQueue.seed', { seed: item.resolved_seed }) }}</p></div><FavoriteTrackButton :track-id="track.id" :label="track.title || batchTitle(batch)" /></div><TrackAudioVersions :track-id="track.id" :fallback-audio-url="track.audio_url" @playing="noteActivity('playing', track.id, $event)" @processing="noteActivity('processing', track.id, $event)" /><StemsPanel :track-id="track.id" :title="track.title" :lyrics="track.lyrics" :model="track.model" /></article></template></div></template></section></template>
    </section>

    <Teleport to="body"><div v-if="showInputs && preview" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" @mousedown.self="showInputs = false"><section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="music-queue-inputs-title" tabindex="-1" class="max-h-[85vh] w-full max-w-3xl space-y-4 overflow-y-auto rounded-2xl border border-border bg-panel p-5"><div class="flex items-start justify-between gap-3"><h2 id="music-queue-inputs-title" class="text-lg font-semibold text-text">{{ t('musicQueue.adaptedInputs') }}</h2><button type="button" class="mq-button" :aria-label="t('common.close')" @click="showInputs = false"><AppIcon name="close" /></button></div><p class="text-sm text-text-dim">{{ t('musicQueue.normalizationHint') }}</p><template v-for="model in preview.models" :key="model.selection.engine"><section class="space-y-3 rounded-xl border border-border p-4"><h3 class="font-medium text-text">{{ label(model.selection.engine) }}</h3><p v-if="model.error_code" class="text-sm text-status-failed">{{ errorText(model.error_code, 'musicQueue.errors.incompatible') }}</p><details v-for="item in preview.items.filter(item => item.engine === model.selection.engine)" :key="`${item.engine}-${item.variation_index}`"><summary class="mq-summary">{{ t('musicQueue.normalizedRequest', { index: item.variation_index }) }}</summary><pre class="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-panel-2 p-3 text-xs text-text-dim">{{ JSON.stringify(item.payload.request, null, 2) }}</pre></details></section></template><p class="text-xs leading-relaxed text-text-dim">{{ t('musicQueue.seedDisclaimer') }}</p></section></div></Teleport>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.mq-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.mq-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2; }
.mq-button { @apply inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium transition-colors hover:bg-panel-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2 disabled:cursor-not-allowed disabled:opacity-50; }
.mq-text-button { @apply inline-flex min-h-11 items-center gap-2 rounded text-xs text-accent2 underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2 disabled:opacity-50; }
.mq-summary { @apply min-h-11 cursor-pointer content-center rounded text-sm text-text-dim focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2; }
</style>
