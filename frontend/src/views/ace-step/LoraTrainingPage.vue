<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from '../../stores/orchestrator'
import { useLoraTrainingStore, loraExportErrorText } from '../../stores/loraTraining'
import { useLoraRegistry } from '../../composables/useLoraRegistry'
import { formatDuration } from '../../composables/formatDuration'
import { preprocessLoraDataset, autoLabelLoraDataset } from '../../api/loraDataset'
import { useLoraDatasetWorkspace } from '../../composables/useLoraDatasetWorkspace'
import DatasetLibrary from '../../components/ace-step/DatasetLibrary.vue'
import type { LoraTrainedFilesResponse, LoraWorkspaceForm } from '../../api/generated'
import * as aceStepApi from '../../api/aceStep'
import * as trainingApi from '../../api/aceStepTraining'
import { ApiError } from '../../api/http'
import ModelOfflineBanner from '../../components/shared/ModelOfflineBanner.vue'
import ProgressBar from '../../components/shared/ProgressBar.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import HelpModal from '../../components/shared/HelpModal.vue'
import type { DatasetSample } from '../../api/aceStepTraining'

const orchestrator = useOrchestratorStore()
const store = useLoraTrainingStore()
const unmatchedWork = computed(() => (store.discoveredAutoLabel?.status === 'running' && store.discoveredAutoLabel.task_id !== store.autoLabelTaskId)
  || (store.discoveredPreprocess?.status === 'running' && store.discoveredPreprocess.task_id !== store.preprocessTaskId)
  || (store.discoveredTraining?.is_training && !store.isTraining))
const { add: addLora } = useLoraRegistry()
const { t, tm } = useI18n()

const form = reactive<Required<LoraWorkspaceForm>>({
  audio_dir: 'datasets/my-voice',
  custom_tag: '',
  tag_position: 'replace',
  all_instrumental: true,
  skip_metas: false,
  format_lyrics: false,
  transcribe_lyrics: false,
  only_unlabeled: true,
  genre_ratio: 0,
  output_dir: '',
  skip_existing: false,
  tensor_dir: '',
  lora_output_dir: './lora_output',
  lora_rank: 64,
  lora_alpha: 128,
  lora_dropout: 0.1,
  learning_rate: 0.0001,
  train_epochs: 10,
  train_batch_size: 1,
  gradient_accumulation: 4,
  save_every_n_epochs: 5,
  training_seed: 42,
  use_fp8: false,
  gradient_checkpointing: false,
  export_path: '',
  export_parent_dir: '',
  registry_name: '',
})

const helpOpen = ref<'dataset' | 'training' | null>(null)

const modelStatus = computed(() => orchestrator.statuses.ace_step?.status ?? 'stopped')
const modelError = computed(() => orchestrator.statuses.ace_step?.error ?? null)
const isRunning = computed(() => modelStatus.value === 'running')

// Auto-labeling and training need the DiT model *and* the LLM actually loaded
// into GPU memory - the orchestrator only reports the process as "running"
// once its HTTP server answers, well before that (lazy on first real use).
// Without this check, clicking "Разметить автоматически" first thing just
// 500s with an opaque "Model not initialized".
const llmReady = ref<boolean | null>(null)
const modelReady = ref<boolean | null>(null)
const checkingReady = ref(false)
const initializing = ref(false)
const initError = ref('')

async function checkModelReady() {
  checkingReady.value = true
  try {
    const health = await aceStepApi.health()
    modelReady.value = health.models_initialized
    llmReady.value = health.llm_initialized
  } catch {
    modelReady.value = null
    llmReady.value = null
  } finally {
    checkingReady.value = false
  }
}

async function onInitModel() {
  initializing.value = true
  initError.value = ''
  try {
    await aceStepApi.initModel(true)
    await checkModelReady()
  } catch (err) {
    initError.value = err instanceof ApiError && err.message === 'native_operation_failed'
      ? t('storeErrors.nativeOperationFailed') : err instanceof Error ? err.message : String(err)
  } finally {
    initializing.value = false
  }
}

watch(isRunning, (running) => {
  if (running) void checkModelReady()
})

onMounted(() => {
  void library.initialize()
  if (isRunning.value) void checkModelReady()
})
onBeforeUnmount(() => store.stopBackgroundTasks())

// ── 1. Dataset ──
const audioDir = toRef(form, 'audio_dir')
const customTag = toRef(form, 'custom_tag')
const tagPosition = toRef(form, 'tag_position')
const allInstrumental = toRef(form, 'all_instrumental')
const loadPath = ref('')

async function onScan() { await library.scan() }
async function onLoadDataset() {
  await library.refresh()
  const entry = library.datasets.value.find(item => item.dataset_path === loadPath.value.trim() && !item.trashed)
  if (entry) await library.select(entry.id)
  else library.error.value = t('loraRecovery.unsafe')
}

// Bulk browser upload: ACE-Step's own dataset API only scans a server-local
// folder path, so files picked/dropped here first go to OpenFabric's own
// backend, which drops them under ACE_STEP_DIR/datasets/<name>/ (a location
// the scan endpoint is already allowed to read) and hands back that path.
const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const uploading = ref(false)
const uploadMessage = ref('')
const uploadError = ref('')

function pickFiles() {
  if (!library.disabled.value && library.selected.value) fileInput.value?.click()
}
function onFilesPicked(e: Event) {
  if (!(e.target instanceof HTMLInputElement)) return
  const files = Array.from(e.target.files || [])
  if (files.length) void handleFiles(files)
  e.target.value = ''
}
function onDrop(e: DragEvent) {
  e.preventDefault()
  dragging.value = false
  const files = Array.from(e.dataTransfer?.files || [])
  if (files.length) void handleFiles(files)
}
async function handleFiles(files: File[]) {
  const entry = library.selected.value
  if (!entry || library.disabled.value || uploading.value) return
  uploading.value = true
  uploadError.value = ''
  uploadMessage.value = ''
  try {
    const res = await library.upload(files)
    if (!res) return
    const skipped = res.skipped.length ? t('lora.uploadedSkipped', { count: res.skipped.length, names: res.skipped.join(', ') }) : ''
    uploadMessage.value = t('lora.uploadedMessage', { saved: res.saved.length, skipped })
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : String(err)
  } finally {
    uploading.value = false
  }
}

const labeledCount = computed(() => store.samples.filter((s) => s.labeled).length)

// ── 2. Auto-label ──
const skipMetas = toRef(form, 'skip_metas')
const formatLyrics = toRef(form, 'format_lyrics')
const transcribeLyrics = toRef(form, 'transcribe_lyrics')
const onlyUnlabeled = toRef(form, 'only_unlabeled')

async function onAutoLabel() {
  if (!await library.saveSnapshot()) return
  const entry = library.selected.value
  if (!entry) return
  await store.startAutoLabel({
    skip_metas: skipMetas.value,
    format_lyrics: formatLyrics.value,
    transcribe_lyrics: transcribeLyrics.value,
    only_unlabeled: onlyUnlabeled.value,
    save_path: store.datasetPath || undefined,
  }, async request => {
    const response = await autoLabelLoraDataset(entry.id, { revision: entry.revision, skip_metas: request.skip_metas, format_lyrics: request.format_lyrics, transcribe_lyrics: request.transcribe_lyrics, only_unlabeled: request.only_unlabeled })
    return { ...response, message: response.message ?? '' }
  })
  await library.syncWorkspace()
}

// ── 3. Review samples ──
const editing = ref<Record<number, Partial<DatasetSample>>>({})
function edited(sample: DatasetSample): Partial<DatasetSample> {
  if (!editing.value[sample.index]) editing.value[sample.index] = { ...sample }
  return editing.value[sample.index]
}
// store.samples gets replaced wholesale after a scan/load/auto-label
// (e.g. auto-label fills in a caption that was empty a moment ago) - drop
// any unsaved edit buffers so the table shows the fresh server data instead
// of stale (often blank) values cached before that happened.
watch(
  () => store.samples,
  () => {
    editing.value = {}
  },
)
async function saveSample(sample: DatasetSample) {
  const e = editing.value[sample.index]
  const entry = library.selected.value
  if (!e || !entry || library.disabled.value) return
  const saved = await library.updateSample(sample.index, {
    caption: e.caption,
    genre: e.genre,
    lyrics: e.lyrics,
    bpm: e.bpm ?? undefined,
    keyscale: e.keyscale,
    timesignature: e.timesignature,
    language: e.language,
    is_instrumental: e.is_instrumental,
  })
  if (saved) delete editing.value[sample.index]
}

const genreRatio = toRef(form, 'genre_ratio')
async function onSaveDataset() { await library.saveSnapshot() }

// ── 4. Preprocess ──
const outputDir = toRef(form, 'output_dir')
function suggestOutputDir() {
  if (!outputDir.value) outputDir.value = `${audioDir.value.replace(/\/$/, '')}/tensors`
}
const skipExisting = toRef(form, 'skip_existing')
async function onPreprocess() {
  suggestOutputDir()
  const entry = library.selected.value
  if (!entry || !await library.saveSnapshot()) return
  const current = library.selected.value
  if (!current) return
  await store.startPreprocess({ output_dir: outputDir.value, skip_existing: skipExisting.value }, async request => {
    const result = await preprocessLoraDataset(current.id, { revision: current.revision, output_dir: request.output_dir, skip_existing: request.skip_existing })
    return { ...result, message: result.message ?? '' }
  })
  await library.syncWorkspace()
}

// ── 5. Training ──
const tensorDir = toRef(form, 'tensor_dir')
const loraOutputDir = toRef(form, 'lora_output_dir')
const loraRank = toRef(form, 'lora_rank')
const loraAlpha = toRef(form, 'lora_alpha')
const loraDropout = toRef(form, 'lora_dropout')
const learningRate = toRef(form, 'learning_rate')
const trainEpochs = toRef(form, 'train_epochs')
const trainBatchSize = toRef(form, 'train_batch_size')
const gradientAccumulation = toRef(form, 'gradient_accumulation')
const saveEveryNEpochs = toRef(form, 'save_every_n_epochs')
const trainingSeed = toRef(form, 'training_seed')
const useFp8 = toRef(form, 'use_fp8')
const gradientCheckpointing = toRef(form, 'gradient_checkpointing')

async function onStartTraining() {
  const dir = tensorDir.value || store.preprocessOutputDir
  if (!dir || library.disabled.value) return
  tensorDir.value = dir
  loraOutputDir.value = `${loraOutputDir.value.replace(/\/run-[0-9a-f-]+\/?$/, '').replace(/\/$/, '')}/run-${crypto.randomUUID()}`
  if (!await library.persist()) return
  await store.startTraining({
    tensor_dir: dir,
    lora_rank: loraRank.value,
    lora_alpha: loraAlpha.value,
    lora_dropout: loraDropout.value,
    learning_rate: learningRate.value,
    train_epochs: trainEpochs.value,
    train_batch_size: trainBatchSize.value,
    gradient_accumulation: gradientAccumulation.value,
    save_every_n_epochs: saveEveryNEpochs.value,
    training_seed: trainingSeed.value,
    lora_output_dir: loraOutputDir.value,
    use_fp8: useFp8.value,
    gradient_checkpointing: gradientCheckpointing.value,
  })
}

const epochProgress = computed(() => {
  if (!store.totalEpochs) return 0
  return ((store.training?.current_epoch ?? 0) / store.totalEpochs) * 100
})
const waitingForFirstUpdate = computed(() => store.isTraining && store.training?.current_step === 0)
const etaLabel = computed(() => {
  if (waitingForFirstUpdate.value) return '—'
  const sec = store.training?.estimated_time_remaining
  if (!sec || !Number.isFinite(sec)) return '—'
  const m = Math.round(sec / 60)
  return m < 1 ? t('lora.etaLessThanMin') : t('lora.etaMin', { value: m })
})

// ── 6. Export ──
const exportParent = toRef(form, 'export_parent_dir')
const registryName = toRef(form, 'registry_name')
const canPickExportFolder = ref(false), exportPreparing = ref(false)
const exportReview = ref<{ request: trainingApi.ExportLoraRequest; result: trainingApi.ExportLoraResponse; name: string } | null>(null)
const exportController = new AbortController()
let exportAlive = true, exportGeneration = 0
const validExportName = computed(() => {
  const name = registryName.value.trim()
  return !!name && name !== '.' && name !== '..' && name.length <= 120 && !/[<>:"/\\|?*\u0000-\u001f\u007f]/.test(name) && !/[. ]$/.test(name)
})
const exportTarget = computed(() => `${exportParent.value.trim().replace(/[\\/]+$/, '')}/${registryName.value.trim()}`)
const exportControlsBusy = computed(() => exportPreparing.value || store.exporting || !!exportReview.value)

onMounted(async () => {
  try {
    const result = await trainingApi.loraExportOptions(exportController.signal)
    if (exportAlive) canPickExportFolder.value = result.can_pick
  } catch { /* Manual folder entry remains available. */ }
})
onBeforeUnmount(() => { exportAlive = false; ++exportGeneration; exportController.abort() })

async function chooseExportFolder() {
  if (exportControlsBusy.value) return
  const generation = ++exportGeneration
  exportPreparing.value = true; store.exportError = ''
  try {
    const result = await trainingApi.pickLoraExportFolder(exportController.signal)
    if (exportAlive && generation === exportGeneration && result.path) exportParent.value = result.path
  } catch (cause) {
    if (exportAlive && generation === exportGeneration) store.exportError = loraExportErrorText(cause)
  } finally {
    if (exportAlive && generation === exportGeneration) exportPreparing.value = false
  }
}
async function onExport() {
  if (exportControlsBusy.value || !exportParent.value.trim() || !validExportName.value || store.nativeBusy) return
  const generation = ++exportGeneration, name = registryName.value.trim()
  const request = { export_path: exportTarget.value, lora_output_dir: loraOutputDir.value }
  exportPreparing.value = true; store.exportError = ''
  try {
    const result = await trainingApi.previewLoraExport(request, exportController.signal)
    if (exportAlive && generation === exportGeneration) exportReview.value = { request: { ...request, export_path: result.export_path }, result, name }
  } catch (cause) {
    if (exportAlive && generation === exportGeneration) store.exportError = loraExportErrorText(cause)
  } finally {
    if (exportAlive && generation === exportGeneration) exportPreparing.value = false
  }
}
async function confirmExport() {
  const review = exportReview.value
  if (!review || store.exporting) return
  try {
    await store.exportAndRegister(review.request.export_path, review.request.lora_output_dir, review.name, addLora)
    if (exportAlive && exportReview.value === review) { exportReview.value = null; registryName.value = '' }
  } catch { /* The store retains the safe, translated error. */ }
}

const library = useLoraDatasetWorkspace(store, form, isRunning)
const checkingTrainedFiles = ref(false), trainedFiles = ref<LoraTrainedFilesResponse | null>(null), trainedFilesError = ref('')
let trainedFilesController: AbortController | undefined, trainedFilesGeneration = 0
function clearTrainedFilesCheck() {
  ++trainedFilesGeneration
  trainedFilesController?.abort(); trainedFilesController = undefined
  checkingTrainedFiles.value = false; trainedFiles.value = null; trainedFilesError.value = ''
}
watch([loraOutputDir, library.selectedId], clearTrainedFilesCheck, { flush: 'sync' })
onBeforeUnmount(clearTrainedFilesCheck)

async function checkTrainedFiles() {
  if (!exportAlive || checkingTrainedFiles.value || exportControlsBusy.value || !loraOutputDir.value.trim()) return
  const generation = ++trainedFilesGeneration
  const controller = new AbortController()
  trainedFilesController = controller
  checkingTrainedFiles.value = true; trainedFiles.value = null; trainedFilesError.value = ''
  try {
    const result = await trainingApi.checkTrainedLoraFiles({ lora_output_dir: loraOutputDir.value }, controller.signal)
    if (exportAlive && generation === trainedFilesGeneration) trainedFiles.value = result
  } catch {
    if (exportAlive && generation === trainedFilesGeneration) trainedFilesError.value = t('loraExport.checkFailed')
  } finally {
    if (exportAlive && generation === trainedFilesGeneration) { checkingTrainedFiles.value = false; trainedFilesController = undefined }
  }
}
</script>

<template>
  <div class="space-y-6">
    <DatasetLibrary :datasets="library.datasets.value" :selected-id="library.selectedId.value" :disabled="library.disabled.value || exportControlsBusy" :loading="library.busy.value" :status="store.autoLabelRunning || store.preprocessRunning || store.isTraining ? t('loraRecovery.active') : library.busy.value ? t('loraRecovery.saving') : t('loraRecovery.saved')" @select="library.select" @create="library.create" @rename="library.rename" @trash="library.trash" @restore="library.restore" />
    <div v-if="library.error.value" role="alert" class="flex flex-wrap items-center gap-3 text-sm text-status-failed"><p>{{ library.error.value }}</p><button type="button" class="min-h-11 rounded-lg border border-border px-3" :disabled="library.busy.value" @click="library.initialize">{{ t('musicLora.refresh') }}</button></div>
    <p v-if="library.issues.value" role="status" class="text-sm text-status-queued">{{ t('loraRecovery.issues', { count: library.issues.value }) }}</p>
    <p v-if="library.notice.value" role="status" class="text-sm text-status-queued">{{ library.notice.value }}</p>
    <p v-if="store.autoLabelReconnecting || store.preprocessReconnecting || store.trainingReconnecting" role="status" class="rounded-xl border border-status-queued/40 p-4 text-sm text-text-dim">{{ t('loraRecovery.reconnecting') }}</p>
    <p v-if="store.autoLabelInterrupted || store.preprocessInterrupted || store.trainingInterrupted" role="status" class="text-sm text-status-queued">{{ t('loraRecovery.interrupted') }}</p>
    <p v-if="unmatchedWork" role="status" class="text-sm text-status-queued">{{ t('loraRecovery.discovered') }}</p>
    <p v-if="store.autoLabelSubmissionUnknown || store.preprocessSubmissionUnknown || store.trainingSubmissionUnknown" role="status" class="text-sm text-status-queued">{{ t('loraRecovery.submissionUnknown') }}</p>
    <ModelOfflineBanner v-if="!isRunning" model-id="ace_step" :status="modelStatus" :error="modelError" />

    <template v-else>
      <div class="rounded-xl border border-status-queued/40 bg-status-queued/10 p-4 text-sm text-text-dim">
        <i18n-t keypath="lora.serverNotice" tag="span">
          <template #example><code class="rounded bg-panel-2 px-1">datasets/...</code></template>
        </i18n-t>
      </div>

      <div v-if="llmReady === false" class="flex flex-wrap items-center gap-3 rounded-xl border border-status-failed/40 bg-status-failed/10 p-4 text-sm text-text-dim">
        <span class="flex-1">
          {{ t('lora.llmNotReady') }}
        </span>
        <button
          type="button"
          class="accent-gradient inline-flex shrink-0 items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          :disabled="initializing"
          @click="onInitModel"
        >
          <span v-if="initializing" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
          {{ initializing ? t('lora.loading') : t('lora.initModel') }}
        </button>
      </div>
      <p v-if="initError" class="text-xs text-status-failed">{{ initError }}</p>

      <!-- 1. Dataset -->
      <section class="space-y-3 rounded-xl border border-border bg-panel p-5">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-semibold text-text">{{ t('lora.step1Title') }}</h2>
          <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'dataset'">{{ t('common.help') }}</button>
        </div>

        <div
          class="flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors"
          :class="dragging ? 'border-accent1 bg-accent1/5' : 'border-border'"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop="onDrop"
          role="button"
          :tabindex="library.disabled.value || !library.selected.value ? -1 : 0"
          :aria-disabled="library.disabled.value || !library.selected.value"
          @keydown.enter.prevent="pickFiles"
          @keydown.space.prevent="pickFiles"
          @click="pickFiles"
        >
          <input ref="fileInput" type="file" multiple accept="audio/*,.wav,.mp3,.flac,.ogg,.opus" class="hidden" @change="onFilesPicked" />
          <p class="text-sm text-text">{{ uploading ? t('lora.loading') : t('lora.dropHint') }}</p>
          <p class="text-xs text-text-dim">{{ t('lora.dropSubHint') }}</p>
        </div>
        <p v-if="uploadMessage" class="text-xs text-status-done">{{ uploadMessage }}</p>
        <p v-if="uploadError" class="text-xs text-status-failed">{{ uploadError }}</p>

        <CollapsibleDetails :summary="t('lora.manualFolderSummary')">
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label class="space-y-1">
            <span class="text-xs text-text-dim">{{ t('lora.audioDirLabel') }}</span>
            <input v-model="audioDir" type="text" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" placeholder="datasets/my-voice" />
          </label>
        </div>
        </CollapsibleDetails>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label class="space-y-1">
            <span class="text-xs text-text-dim">{{ t('lora.triggerWordLabel') }}</span>
            <input v-model="customTag" type="text" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('lora.triggerWordPlaceholder')" />
          </label>
          <label class="space-y-1">
            <span class="text-xs text-text-dim">{{ t('lora.triggerPositionLabel') }}</span>
            <select v-model="tagPosition" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
              <option value="replace">{{ t('lora.tagReplace') }}</option>
              <option value="prepend">{{ t('lora.tagPrepend') }}</option>
              <option value="append">{{ t('lora.tagAppend') }}</option>
            </select>
          </label>
        </div>
        <label class="flex items-center gap-2 text-sm text-text-dim">
          <input v-model="allInstrumental" type="checkbox" class="accent-accent1" />
          {{ t('lora.allInstrumental') }}
        </label>
        <p class="text-xs text-text-dim">{{ t('lora.clipTrimHint') }}</p>
        <div class="flex flex-wrap items-center gap-3">
          <button type="button" class="accent-gradient rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="library.disabled.value || !library.selected.value" @click="onScan">
            {{ store.scanning ? t('lora.scanning') : t('lora.scanFolder') }}
          </button>
          <span class="text-text-dim">{{ t('lora.or') }}</span>
          <input v-model="loadPath" type="text" class="w-56 rounded-lg border border-border bg-panel-2 p-1.5 text-xs text-text" :placeholder="t('lora.loadPathPlaceholder')" />
          <button type="button" class="rounded-lg bg-panel-2 px-3 py-1.5 text-xs text-text-dim hover:text-text" :disabled="library.disabled.value" @click="onLoadDataset">{{ t('lora.load') }}</button>
        </div>
        <p v-if="store.scanError" class="text-xs text-status-failed">{{ store.scanError }}</p>
        <p v-if="store.samples.length" class="text-sm text-text-dim">
          {{ t('lora.samplesFound', { count: store.samples.length, labeled: labeledCount }) }}
        </p>
      </section>

      <template v-if="store.samples.length">
        <!-- 2. Auto-label -->
        <section class="space-y-3 rounded-xl border border-border bg-panel p-5">
          <h2 class="text-lg font-semibold text-text">{{ t('lora.step2Title') }}</h2>
          <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label class="flex items-center gap-2 text-sm text-text-dim"><input v-model="onlyUnlabeled" type="checkbox" class="accent-accent1" /> {{ t('lora.onlyUnlabeled') }}</label>
            <label class="flex items-center gap-2 text-sm text-text-dim"><input v-model="skipMetas" type="checkbox" class="accent-accent1" /> {{ t('lora.skipMetas') }}</label>
            <label class="flex items-center gap-2 text-sm text-text-dim"><input v-model="formatLyrics" type="checkbox" class="accent-accent1" /> {{ t('lora.formatLyrics') }}</label>
            <label class="flex items-center gap-2 text-sm text-text-dim"><input v-model="transcribeLyrics" type="checkbox" class="accent-accent1" /> {{ t('lora.transcribeLyrics') }}</label>
          </div>
          <button
            type="button"
            class="accent-gradient inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            :disabled="library.disabled.value || !library.selected.value"
            @click="onAutoLabel"
          >
            <span v-if="store.autoLabelStarting || store.autoLabelRunning" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
            {{ store.autoLabelStarting ? t('lora.starting') : store.autoLabelRunning ? t('lora.labeling') : onlyUnlabeled ? t('loraRecovery.resumeLabel') : t('lora.labelAuto') }}
          </button>
          <div v-if="store.autoLabelRunning || store.autoLabelTotal" class="space-y-2">
            <ProgressBar :value="store.autoLabelTotal ? (store.autoLabelCurrent / store.autoLabelTotal) * 100 : 0" />
            <p class="text-xs text-text-dim">{{ store.autoLabelCurrent }} / {{ store.autoLabelTotal }} — {{ store.autoLabelProgressMsg }}</p>
            <p v-if="store.autoLabelLastSample" class="text-xs text-text-dim">
              {{ t('lora.lastLabeled', { filename: store.autoLabelLastSample.filename, caption: store.autoLabelLastSample.caption || '—' }) }}
            </p>
          </div>
          <p v-if="store.autoLabelError" class="text-xs text-status-failed">{{ store.autoLabelError }}</p>
        </section>

        <!-- 3. Review samples -->
        <section class="space-y-3 rounded-xl border border-border bg-panel p-5">
          <h2 class="text-lg font-semibold text-text">{{ t('lora.step3Title') }}</h2>
          <div class="max-h-96 overflow-auto rounded-lg border border-border">
            <table class="w-full text-left text-xs">
              <thead class="sticky top-0 bg-panel-2 text-text-dim">
                <tr>
                  <th class="p-2">{{ t('lora.colFile') }}</th>
                  <th class="p-2">{{ t('lora.colDuration') }}</th>
                  <th class="p-2">✓</th>
                  <th class="p-2">{{ t('lora.colDescription') }}</th>
                  <th class="p-2">{{ t('lora.colGenre') }}</th>
                  <th class="p-2">BPM</th>
                  <th class="p-2">{{ t('lora.colKey') }}</th>
                  <th class="p-2">{{ t('lora.colInstrumental') }}</th>
                  <th class="p-2"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in store.samples" :key="s.index" class="border-t border-border/60 align-top">
                  <td class="p-2 text-text-dim">{{ s.filename }}</td>
                  <td class="p-2 text-text-dim">{{ formatDuration(s.duration) }}</td>
                  <td class="p-2">{{ s.labeled ? '✓' : '—' }}</td>
                  <td class="p-2"><input v-model="edited(s).caption" type="text" class="w-48 rounded border border-border bg-panel-2 p-1 text-text" /></td>
                  <td class="p-2"><input v-model="edited(s).genre" type="text" class="w-28 rounded border border-border bg-panel-2 p-1 text-text" /></td>
                  <td class="p-2"><input v-model.number="edited(s).bpm" type="number" class="w-16 rounded border border-border bg-panel-2 p-1 text-text" /></td>
                  <td class="p-2"><input v-model="edited(s).keyscale" type="text" class="w-16 rounded border border-border bg-panel-2 p-1 text-text" /></td>
                  <td class="p-2"><input v-model="edited(s).is_instrumental" type="checkbox" class="accent-accent1" /></td>
                  <td class="p-2"><button type="button" class="rounded bg-panel-2 px-2 py-1 text-text-dim hover:text-text disabled:opacity-50" :disabled="library.disabled.value" @click="saveSample(s)">{{ t('lora.save') }}</button></td>
                </tr>
              </tbody>
            </table>
          </div>

          <CollapsibleDetails :summary="t('lora.saveDatasetSummary')">
            <label class="space-y-1 block">
              <span class="text-xs text-text-dim">{{ t('lora.genreRatioLabel', { value: genreRatio }) }}</span>
              <input v-model.number="genreRatio" type="range" min="0" max="100" step="5" class="w-full accent-accent1" />
            </label>
            <div class="flex gap-2">
              <span class="flex-1 break-all text-xs text-text-dim">{{ store.datasetPath }}</span>
              <button type="button" class="rounded-lg bg-panel-2 px-3 py-1.5 text-xs text-text-dim hover:text-text" @click="onSaveDataset">{{ t('lora.save') }}</button>
            </div>
          </CollapsibleDetails>
        </section>

        <!-- 4. Preprocess -->
        <section class="space-y-3 rounded-xl border border-border bg-panel p-5">
          <h2 class="text-lg font-semibold text-text">{{ t('lora.step4Title') }}</h2>
          <p class="text-xs text-text-dim">{{ t('lora.preprocessHint') }}</p>
          <p class="text-xs text-text-dim">{{ t('loraRecovery.reuseHint') }}</p>
          <div class="flex flex-wrap items-center gap-3">
            <input v-model="outputDir" type="text" class="w-64 rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('lora.outputDirPlaceholder')" @focus="suggestOutputDir" />
            <label class="flex items-center gap-2 text-sm text-text-dim"><input v-model="skipExisting" type="checkbox" class="accent-accent1" /> {{ t('lora.skipExisting') }}</label>
            <button
              type="button"
              class="accent-gradient inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              :disabled="library.disabled.value || !library.selected.value"
              @click="onPreprocess"
            >
              <span v-if="store.preprocessStarting || store.preprocessRunning" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
              {{ store.preprocessStarting ? t('lora.starting') : store.preprocessRunning ? t('lora.processing') : t('lora.startPreprocess') }}
            </button>
          </div>
          <div v-if="store.preprocessRunning || store.preprocessTotal" class="space-y-2">
            <ProgressBar :value="store.preprocessTotal ? (store.preprocessCurrent / store.preprocessTotal) * 100 : 0" />
            <p class="text-xs text-text-dim">{{ t('lora.tensorsProgress', { current: store.preprocessCurrent, total: store.preprocessTotal }) }}</p>
          </div>
          <p v-if="store.preprocessError" class="text-xs text-status-failed">{{ store.preprocessError }}</p>
          <p v-if="store.preprocessOutputDir" class="text-xs text-text-dim">{{ t('lora.done', { path: store.preprocessOutputDir }) }}</p>
        </section>

      </template>

      <template v-if="store.samples.length || store.training?.is_training || store.trainingInterrupted || (store.training?.current_step ?? 0) > 0 || store.preprocessOutputDir || tensorDir">
        <!-- 5. Training -->
        <section class="space-y-3 rounded-xl border border-border bg-panel p-5">
          <div class="flex items-center justify-between">
            <h2 class="text-lg font-semibold text-text">{{ t('lora.step5Title') }}</h2>
            <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'training'">{{ t('common.help') }}</button>
          </div>
          <p class="text-xs leading-relaxed text-text-dim">{{ t('loraRecovery.checkpointLimit') }}</p>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label class="space-y-1">
              <span class="text-xs text-text-dim">{{ t('lora.tensorDirLabel') }}</span>
              <input v-model="tensorDir" type="text" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="store.preprocessOutputDir || 'datasets/my-voice/tensors'" />
            </label>
            <label class="space-y-1">
              <span class="text-xs text-text-dim">{{ t('lora.outputDirLabel') }}</span>
              <input v-model="loraOutputDir" type="text" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" />
            </label>
          </div>

          <CollapsibleDetails :summary="t('lora.advancedParams')">
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.loraRank') }}</span><input v-model.number="loraRank" type="number" min="1" max="256" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.loraAlpha') }}</span><input v-model.number="loraAlpha" type="number" min="1" max="512" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.dropout') }}</span><input v-model.number="loraDropout" type="number" min="0" max="1" step="0.05" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.learningRate') }}</span><input v-model.number="learningRate" type="number" min="0" step="0.00001" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.epochs') }}</span><input v-model.number="trainEpochs" type="number" min="1" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.batchSize') }}</span><input v-model.number="trainBatchSize" type="number" min="1" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.gradientAccumulation') }}</span><input v-model.number="gradientAccumulation" type="number" min="1" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.saveEveryN') }}</span><input v-model.number="saveEveryNEpochs" type="number" min="1" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
              <label class="space-y-1"><span class="text-xs text-text-dim">{{ t('lora.seed') }}</span><input v-model.number="trainingSeed" type="number" class="w-full rounded-lg border border-border bg-panel-2 p-1.5 text-sm text-text" /></label>
            </div>
            <div class="flex gap-4">
              <label class="flex items-center gap-2 text-sm text-text-dim"><input v-model="useFp8" type="checkbox" class="accent-accent1" /> {{ t('lora.fp8') }}</label>
              <label class="flex items-center gap-2 text-sm text-text-dim"><input v-model="gradientCheckpointing" type="checkbox" class="accent-accent1" /> {{ t('lora.gradientCheckpointing') }}</label>
            </div>
          </CollapsibleDetails>

          <div class="flex flex-wrap items-center gap-3">
            <button
              type="button"
              class="accent-gradient inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              :disabled="library.disabled.value || (!tensorDir && !store.preprocessOutputDir)"
              @click="onStartTraining"
            >
              <span v-if="store.trainingStarting" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
              {{ store.trainingStarting ? t('lora.starting') : t('lora.startTraining') }}
            </button>
            <button v-if="store.isTraining" type="button" class="rounded-lg border border-status-failed px-4 py-2 text-sm font-medium text-status-failed" @click="store.stopTraining">
              {{ t('lora.stop') }}
            </button>
            <button type="button" :disabled="checkingTrainedFiles || exportControlsBusy || !loraOutputDir.trim()" :aria-busy="checkingTrainedFiles" aria-describedby="lora-trained-files-status" class="min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text disabled:opacity-50" @click="checkTrainedFiles">{{ t(checkingTrainedFiles ? 'loraExport.checkingFiles' : 'loraExport.checkFiles') }}</button>
          </div>
          <p v-if="store.trainingError" class="text-xs text-status-failed">{{ store.trainingError }}</p>

          <div v-if="store.training && (store.isTraining || store.training.current_step > 0)" class="space-y-2 rounded-lg border border-border bg-panel-2/50 p-3">
            <ProgressBar :value="epochProgress" />
            <p class="text-xs text-text-dim">
              {{ t('lora.epochStatus', {
                epoch: store.training.current_epoch,
                total: store.totalEpochs ? ` / ${store.totalEpochs}` : '',
                step: store.training.current_step,
                loss: !waitingForFirstUpdate && store.training.current_loss != null ? store.training.current_loss.toFixed(4) : '—',
                eta: etaLabel,
              }) }}
            </p>
            <p v-if="waitingForFirstUpdate" role="status" class="text-xs text-text-dim">{{ t('loraRecovery.firstUpdate') }}</p>
            <p class="text-xs text-text-dim">{{ store.training.status }}</p>
            <p v-if="waitingForFirstUpdate && store.training.tensorboard_url" class="text-xs text-text-dim">{{ t('loraRecovery.tensorboardPending') }}</p>
            <a v-else-if="store.training.tensorboard_url" :href="store.training.tensorboard_url" target="_blank" rel="noopener" class="text-xs text-accent1 hover:underline">{{ t('lora.openTensorboard') }}</a>
          </div>
        </section>

      </template>
    </template>

    <div id="lora-trained-files-status" v-show="checkingTrainedFiles || trainedFiles || trainedFilesError" aria-live="polite" aria-atomic="true" class="space-y-2 rounded-xl border border-border bg-panel p-4 text-sm">
      <p v-if="checkingTrainedFiles" class="text-text-dim">{{ t('loraExport.checkingFiles') }}</p>
      <div v-if="trainedFiles" data-trained-files-result class="space-y-2">
        <template v-if="trainedFiles.checkpoint">
          <p class="font-semibold text-status-done">{{ t(trainedFiles.checkpoint.kind === 'final' ? 'loraExport.finalFound' : 'loraExport.checkpointFound') }}</p>
          <p class="break-all font-mono text-xs text-text">{{ trainedFiles.checkpoint.source }}</p>
          <p class="text-xs text-text-dim">{{ t('loraExport.foundHint') }}</p>
          <a href="#lora-export" class="inline-flex min-h-11 items-center text-accent1 hover:underline">{{ t('loraExport.goToExport') }}</a>
        </template>
        <p v-else class="text-text-dim">{{ t('loraExport.noFiles') }}</p>
      </div>
      <p v-if="trainedFilesError" data-trained-files-error role="alert" class="text-status-failed">{{ trainedFilesError }}</p>
    </div>

        <!-- 6. Export -->
        <section id="lora-export" class="space-y-3 rounded-xl border border-border bg-panel p-5">
          <h2 class="text-lg font-semibold text-text">{{ t('lora.step6Title') }}</h2>
          <p class="text-xs text-text-dim">{{ t('loraExport.hint') }}</p>
          <div class="space-y-1">
            <label for="lora-export-source" class="text-xs text-text-dim">{{ t('loraExport.trainingRun') }}</label>
            <span class="flex flex-wrap gap-2">
              <input id="lora-export-source" v-model="loraOutputDir" name="lora-export-source" type="text" :disabled="exportControlsBusy" class="min-w-0 flex-1 rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" spellcheck="false" />
              <button type="button" :disabled="checkingTrainedFiles || exportControlsBusy || !loraOutputDir.trim()" :aria-busy="checkingTrainedFiles" aria-describedby="lora-trained-files-status" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="checkTrainedFiles">{{ t(checkingTrainedFiles ? 'loraExport.checkingFiles' : 'loraExport.checkFiles') }}</button>
            </span>
          </div>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label class="space-y-1">
              <span class="text-xs text-text-dim">{{ t('loraExport.parent') }}</span>
              <span class="flex gap-2">
                <input v-model="exportParent" name="lora-export-parent" type="text" :disabled="exportControlsBusy" class="min-w-0 flex-1 rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" spellcheck="false" />
                <button v-if="canPickExportFolder" type="button" :disabled="exportControlsBusy" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="chooseExportFolder">{{ t('loraExport.choose') }}</button>
              </span>
              <span v-if="!canPickExportFolder" class="text-xs text-text-dim">{{ t('loraExport.manual') }}</span>
            </label>
            <label class="space-y-1">
              <span class="text-xs text-text-dim">{{ t('loraExport.name') }}</span>
              <input v-model="registryName" name="lora-export-name" type="text" maxlength="120" :disabled="exportControlsBusy" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" placeholder="my-voice" />
              <span v-if="registryName && !validExportName" class="text-xs text-status-failed">{{ t('loraExport.nameHint') }}</span>
            </label>
          </div>
          <p v-if="exportParent.trim() && validExportName" class="break-all text-xs text-text-dim">{{ t('loraExport.destination') }}: <span class="font-mono">{{ exportTarget }}</span></p>
          <button type="button" class="accent-gradient rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="exportControlsBusy || store.nativeBusy || !exportParent.trim() || !validExportName" @click="onExport">
            {{ store.exporting ? t('lora.exporting') : exportPreparing ? t('loraExport.preparing') : t('lora.exportAndRegister') }}
          </button>
          <p v-if="store.exportError" role="alert" class="text-xs text-status-failed">{{ store.exportError }}</p>
          <p v-if="store.lastExportedPath" class="text-xs text-status-done">{{ t('lora.registered', { path: store.lastExportedPath }) }}</p>
        </section>
    <HelpModal :open="!!exportReview" :title="t('loraExport.review')" @close="!store.exporting && (exportReview = null)">
      <template v-if="exportReview">
        <p>{{ t('loraExport.destination') }}</p><p class="break-all font-mono text-text">{{ exportReview.result.export_path }}</p>
        <p>{{ t('loraExport.source') }}</p><p class="break-all font-mono">{{ exportReview.result.source }}</p>
        <p class="rounded-lg border border-border p-3 text-text">{{ t('loraExport.noDeletion') }}</p>
        <p v-if="store.exportError" role="alert" class="text-status-failed">{{ store.exportError }}</p>
        <div class="flex flex-wrap gap-3">
          <button type="button" :disabled="store.exporting" class="min-h-11 rounded-lg border border-border px-4 text-text disabled:opacity-50" @click="exportReview = null">{{ t('loraRecovery.cancel') }}</button>
          <button type="button" :disabled="store.exporting" class="accent-gradient min-h-11 rounded-lg px-4 font-semibold text-white disabled:opacity-50" @click="confirmExport">{{ store.exporting ? t('lora.exporting') : t('loraExport.confirm') }}</button>
        </div>
      </template>
    </HelpModal>

    <HelpModal :open="helpOpen === 'dataset'" :title="t('lora.help.dataset.title')" @close="helpOpen = null">
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('lora.help.dataset.fieldHeader') }}</th>
            <th class="py-1">{{ t('lora.help.dataset.whatHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('lora.help.dataset.rows') as { field: string; what: string }[])" :key="row.field" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-medium text-text">{{ row.field }}</td>
            <td class="py-1.5">{{ row.what }}</td>
          </tr>
        </tbody>
      </table>
    </HelpModal>

    <HelpModal :open="helpOpen === 'training'" :title="t('lora.help.training.title')" @close="helpOpen = null">
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('lora.help.training.paramHeader') }}</th>
            <th class="py-1">{{ t('lora.help.training.whatHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('lora.help.training.rows') as { param: string; what: string }[])" :key="row.param" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-medium text-text">{{ row.param }}</td>
            <td class="py-1.5">{{ row.what }}</td>
          </tr>
        </tbody>
      </table>
      <p class="text-xs text-text-dim">{{ t('lora.help.training.footer') }}</p>
    </HelpModal>
  </div>
</template>
