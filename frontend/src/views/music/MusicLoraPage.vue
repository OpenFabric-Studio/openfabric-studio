<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import * as library from '../../api/musicLora'
import { musicAdapterFileUrl, musicLoraNameKey } from '../../api/musicLora'
import * as training from '../../api/stableLora'
import { ApiError } from '../../api/http'
import { pendingMusicAdapter } from '../../composables/generationDrafts'
import type { MusicAdapter, MusicLoraCapabilities, StableLoraSample, StableLoraTrainingJob, StableLoraTrainingRequest, StableLoraTrainingStatus } from '../../api/generated'
import { createPollingLoop } from '../../composables/polling'
import LibraryPicker from '../../components/editor/LibraryPicker.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import MusicTrainingSetup from './MusicTrainingSetup.vue'
import TrainingDatasetPicker from './TrainingDatasetPicker.vue'
import { savedTrainingSamples, trainingSampleKey } from '../../composables/musicTrainingSamples'
import type { LoraDatasetOperationResponse } from '../../api/generated'
import ModelLoraTraining from './ModelLoraTraining.vue'
import TrainingOptionsHelp from './TrainingOptionsHelp.vue'
import TrainingJobProgress from './TrainingJobProgress.vue'

const props = withDefaults(defineProps<{ engine?: Exclude<MusicLoraCapabilities['engines'][number]['engine'], 'ace_step'> }>(), { engine: 'stable_audio3' })
const { t } = useI18n()
const capabilities = ref<MusicLoraCapabilities['engines']>([]), adapters = ref<MusicAdapter[]>([])
const status = ref<StableLoraTrainingStatus | null>(null), jobs = ref<StableLoraTrainingJob[]>([])
const historyKnown = ref(false), reconnecting = ref(false)
const adapterHistoryIncomplete = ref(false), trainingHistoryIncomplete = ref(false), busy = ref(false), error = ref('')
const nameArea = ref<'training' | 'import' | null>(null)
const nameError = computed(() => [t('musicLora.errors.nameExists'), t('musicLora.errors.nameUnavailable')].includes(error.value) ? error.value : '')
const importEngine = computed<MusicAdapter['engine']>(() => props.engine)
const importTarget = ref<MusicAdapter['target']>(props.engine === 'yue2' ? 'ar' : 'small-music'), importName = ref(''), importFile = ref<File | null>(null)
const modelCapabilities = computed(() => capabilities.value.filter(value => value.engine === props.engine))
const modelAdapters = computed(() => adapters.value.filter(value => value.engine === props.engine))
const modelHistoryNames = ref<string[]>([])
const knownNames = computed(() => [...modelAdapters.value.map(adapter => adapter.name), ...jobs.value.map(job => job.request.name), ...modelHistoryNames.value].map(musicLoraNameKey))
const trainingNameTaken = computed(() => !!musicLoraNameKey(name.value) && knownNames.value.includes(musicLoraNameKey(name.value)))
const importNameTaken = computed(() => !!musicLoraNameKey(importName.value) && knownNames.value.includes(musicLoraNameKey(importName.value)))
const trainingNameError = computed(() => trainingNameTaken.value ? t('musicLora.errors.nameExists') : nameArea.value === 'training' ? nameError.value : '')
const importNameError = computed(() => importNameTaken.value ? t('musicLora.errors.nameExists') : nameArea.value === 'import' ? nameError.value : '')
const fileInput = ref<HTMLInputElement | null>(null)
const importTargets = computed<MusicAdapter['target'][]>(() => importEngine.value === 'stable_audio3' ? ['small-music', 'medium'] : ['ar', 'nar'])
const canImport = computed(() => !importNameTaken.value && modelCapabilities.value.some(value => value.loading) && !!importFile.value && /\.safetensors$/i.test(importFile.value.name) && importFile.value.size > 8 && importFile.value.size <= 512 * 1024 * 1024 && importName.value.trim().length > 0 && importName.value.length <= 120)
const name = ref(''), variant = ref<NonNullable<StableLoraTrainingRequest['variant']>>('small-music')
const samples = ref<Array<StableLoraSample & { title: string }>>([]), pickerOpen = ref(false)
const selectedTrackIds = computed(() => samples.value.flatMap(sample => sample.track_id == null ? [] : [sample.track_id]))
const selectionMessage = ref('')
const maxSteps = ref(1000), rank = ref<NonNullable<StableLoraTrainingRequest['rank']>>(16), learningRate = ref(0.0001), seed = ref(42)
const active = computed(() => jobs.value.some(ongoing))
const weightsReady = computed(() => variant.value === 'small-music' ? status.value?.small_weights_ready : status.value?.medium_weights_ready)
const stableCapability = computed(() => capabilities.value.find(engine => engine.engine === 'stable_audio3'))
const ready = computed(() => status.value?.supported && status.value.installed && weightsReady.value && stableCapability.value?.training)
const setupRequired = computed(() => status.value && (!status.value.supported || !status.value.installed || !weightsReady.value || stableCapability.value?.training === false))
const canTrain = computed(() => ready.value && historyKnown.value && !active.value && !trainingNameTaken.value && name.value.trim().length > 0 && name.value.length <= 120 && samples.value.length > 0 && samples.value.length <= 100
  && samples.value.every(sample => sample.caption.trim().length > 0 && sample.caption.length <= 4000)
  && Number.isInteger(maxSteps.value) && maxSteps.value >= 1 && maxSteps.value <= 100000
  && Number.isFinite(learningRate.value) && learningRate.value >= 0.0000001 && learningRate.value <= 0.01
  && Number.isInteger(seed.value) && seed.value >= 0 && seed.value <= 2147483647)
const setupMessage = computed(() => {
  if (!status.value) return t(busy.value ? 'musicLora.simple.checkingSetup' : 'musicLora.simple.unknownSetup')
  if (!status.value.supported) return t('musicLora.unsupportedPlatform')
  if (!status.value.installed) return t('musicLora.missingRuntime')
  if (!weightsReady.value) return t('musicLora.missingWeights')
  if (!stableCapability.value) return t('musicLora.simple.unknownSetup')
  return t(ready.value ? 'musicLora.simple.ready' : 'musicLora.simple.trainingUnavailable')
})
const trainingRequirement = computed(() => {
  if (active.value) return t('musicLora.simple.activeRun')
  if (!historyKnown.value) return t('musicLora.simple.checkHistory')
  if (!ready.value) return t(setupRequired.value ? 'musicLora.simple.finishSetup' : 'musicLora.simple.refreshSetup')
  if (!name.value.trim()) return t('musicLora.simple.enterName')
  if (trainingNameTaken.value) return t('musicLora.errors.nameExists')
  if (!samples.value.length) return t('musicLora.simple.addTracks')
  if (samples.value.some(sample => !sample.caption.trim())) return t('musicLora.simple.addCaptions')
  return canTrain.value ? '' : t('musicLora.simple.checkSettings')
})
let alive = true, generation = 0, controller: AbortController | undefined
function ongoing(job: StableLoraTrainingJob): boolean { return job.status === 'queued' || job.status === 'running' }
function errorText(code: string | null | undefined, fallback: string): string {
  switch (code) {
    case 'music_lora_name_exists': return t('musicLora.errors.nameExists')
    case 'music_lora_name_unavailable': return t('musicLora.errors.nameUnavailable')
    case 'music_lora_format_invalid':
    case 'music_lora_target_mismatch': return t('musicLora.errors.format')
    case 'music_lora_adapter_not_found': return t('musicLora.errors.missing')
    case 'music_lora_adapter_changed': return t('musicLora.errors.changed')
    case 'stable_lora_runtime_unsupported': return t('musicLora.unsupportedPlatform')
    case 'stable_lora_engine_not_installed': return t('musicLora.missingRuntime')
    case 'stable_lora_training_weights_missing': return t('musicLora.missingWeights')
    case 'module_setup_busy':
    case 'stable_lora_busy': return t('musicLora.errors.busy')
    case 'stable_lora_track_not_found':
    case 'stable_lora_dataset_invalid': return t('musicLora.errors.track')
    default: return t(fallback)
  }
}
const poll = createPollingLoop(async context => {
  try {
    const response = await training.listStableLora(context.signal)
    if (!alive || !context.isCurrent()) return false
    jobs.value = response.jobs; historyKnown.value = true; reconnecting.value = false; trainingHistoryIncomplete.value = response.history_incomplete ?? false
    if (error.value === t('musicLora.errors.training')) error.value = ''
  } catch { if (alive && context.isCurrent()) reconnecting.value = true }
  return active.value || !historyKnown.value
}, 2500)
async function action(work: (signal: AbortSignal, isCurrent: () => boolean) => Promise<void>, fallback: string) {
  if (!alive || busy.value) return
  poll.stop(); const token = ++generation, request = new AbortController()
  const isCurrent = () => alive && token === generation && !request.signal.aborted
  controller = request; busy.value = true; error.value = ''
  try { await work(request.signal, isCurrent) }
  catch (cause) { if (isCurrent()) error.value = errorText(cause instanceof ApiError ? cause.message : null, fallback) }
  finally { if (isCurrent()) { busy.value = false; controller = undefined; if (props.engine === 'stable_audio3' && (active.value || !historyKnown.value)) poll.start(false) } }
}
function refresh() {
  return action(async (signal, isCurrent) => {
    const results = await Promise.allSettled([library.musicLoraCapabilities(signal), library.listMusicAdapters(signal), props.engine === 'stable_audio3' ? training.stableLoraStatus(signal) : Promise.resolve(null), props.engine === 'stable_audio3' ? training.listStableLora(signal) : Promise.resolve(null)])
    if (!isCurrent()) return
    const [capability, registry, setup, history] = results
    if (capability.status === 'fulfilled') capabilities.value = capability.value.engines
    if (registry.status === 'fulfilled' && registry.value) { adapters.value = registry.value.adapters; adapterHistoryIncomplete.value = registry.value.history_incomplete ?? false }
    if (setup.status === 'fulfilled' && setup.value) status.value = setup.value
    if (history.status === 'fulfilled' && history.value) { jobs.value = history.value.jobs; historyKnown.value = true; reconnecting.value = false; trainingHistoryIncomplete.value = history.value.history_incomplete ?? false }
    if (history.status === 'rejected') reconnecting.value = true
    if ([capability, registry, setup].some(result => result.status === 'rejected')) error.value = t('musicLora.errors.load')
  }, 'musicLora.errors.load')
}
function fileChanged(event: Event) {
  const target = event.target
  if (target instanceof HTMLInputElement) importFile.value = target.files?.[0] ?? null
}
function importAdapter() {
  if (!canImport.value || !importFile.value) return
  const file = importFile.value, metadata = { engine: importEngine.value, target: importTarget.value, name: importName.value.trim() }
  nameArea.value = 'import'
  return action(async (signal, isCurrent) => {
    const response = await library.importMusicAdapter(file, metadata, signal)
    if (!isCurrent()) return
    adapters.value = [response, ...adapters.value.filter(adapter => adapter.id !== response.id)]
    importFile.value = null; importName.value = ''; if (fileInput.value) fileInput.value.value = ''
  }, 'musicLora.errors.import')
}
function pickTrack(track: { trackId?: number; sourceLabel: string }) {
  pickerOpen.value = false
  if (track.trackId === undefined || !Number.isSafeInteger(track.trackId) || track.trackId < 1 || samples.value.length >= 100 || samples.value.some(sample => sample.track_id === track.trackId)) return
  samples.value.push({ track_id: track.trackId, caption: '', title: track.sourceLabel })
  selectionMessage.value = t('musicLora.selection.addedTrack', { title: track.sourceLabel })
}
function addDataset(snapshot: LoraDatasetOperationResponse) {
  const selected = savedTrainingSamples(snapshot, props.engine, 100 - samples.value.length)
  const existing = new Set(samples.value.map(trainingSampleKey))
  const added = selected.filter(sample => !existing.has(trainingSampleKey(sample)))
  samples.value.push(...added)
  selectionMessage.value = t(added.length ? 'musicLora.selection.addedDataset' : 'musicLora.selection.alreadySelected', { count: added.length, name: snapshot.dataset.name })
}
function removeSample(sample: StableLoraSample) {
  samples.value = samples.value.filter(item => trainingSampleKey(item) !== trainingSampleKey(sample))
  selectionMessage.value = ''
}

function start() {
  if (!canTrain.value) return
  const request: StableLoraTrainingRequest = { name: name.value.trim(), variant: variant.value, samples: samples.value.map(sample => ({ ...(sample.dataset ? { dataset: sample.dataset } : { track_id: sample.track_id }), caption: sample.caption.trim() })), max_steps: maxSteps.value, rank: rank.value, learning_rate: learningRate.value, seed: seed.value }
  nameArea.value = 'training'
  return action(async (signal, isCurrent) => {
    historyKnown.value = false
    const response = await training.submitStableLora(request, signal)
    if (isCurrent()) { jobs.value = [response, ...jobs.value.filter(job => job.id !== response.id)]; historyKnown.value = true }
  }, 'musicLora.errors.training')
}
function preview(job: StableLoraTrainingJob, withAdapter: boolean) {
  if (job.adapter) pendingMusicAdapter.value = { engine: job.adapter.engine, target: job.adapter.target, selection: withAdapter ? { id: job.adapter.id, strength: 1 } : null }
}

function cancel(job: StableLoraTrainingJob) {
  return action(async (signal, isCurrent) => {
    const response = await training.cancelStableLora(job.id, signal)
    if (isCurrent()) jobs.value = jobs.value.map(existing => existing.id === response.id ? response : existing)
  }, 'musicLora.errors.training')
}
onMounted(() => { void refresh() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <div class="min-w-0 space-y-5" :aria-busy="busy">
    <header class="space-y-2">
      <h2 class="text-xl font-semibold text-text">{{ t(`musicLora.engines.${props.engine}`) }}</h2>
      <p class="max-w-3xl text-sm leading-relaxed text-text-dim">{{ t(props.engine === 'stable_audio3' ? 'musicLora.simple.intro' : `loraWorkspace.models.${props.engine}`) }}</p>
    </header>
    <p v-if="busy" role="status" class="text-sm text-text-dim">{{ t('musicLora.working') }}</p>
    <p v-if="reconnecting" role="status" class="text-sm text-status-queued">{{ t('loraWorkspace.reconnecting') }}</p>
    <p v-if="error && !nameError" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="adapterHistoryIncomplete || trainingHistoryIncomplete" role="alert" class="text-sm text-status-queued">{{ t('musicLora.historyIncomplete') }}</p>

    <div v-if="props.engine === 'stable_audio3'" data-stable-readiness data-engine="stable_audio3" class="space-y-2 rounded-xl border border-border bg-panel p-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p role="status" :class="ready ? 'text-status-done' : 'text-status-queued'" class="text-sm font-medium">{{ setupMessage }}</p>
        <div class="flex flex-wrap items-center gap-4">
          <RouterLink v-if="setupRequired" to="/settings#module-stable_audio3" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.setup') }}</RouterLink>
          <button type="button" class="lora-button" :disabled="busy" @click="refresh">{{ t('musicLora.refresh') }}</button>
        </div>
      </div>
      <MusicTrainingSetup v-if="status" :variant="variant" :supported="status.supported" :installed="status.installed" :ready="!!weightsReady" @refresh="refresh" />
    </div>
    <ModelLoraTraining v-else :engine="props.engine" :managed-names="modelAdapters.map(adapter => adapter.name)" @registered="refresh" @history-names="modelHistoryNames = $event" />
    <section v-if="props.engine !== 'stable_audio3'" class="space-y-3 rounded-xl border border-border bg-panel p-5">
      <div class="flex flex-wrap items-center justify-between gap-3"><h3 class="font-semibold text-text">{{ t('musicLora.capability') }}</h3><button type="button" class="lora-button" :disabled="busy" @click="refresh">{{ t('musicLora.refresh') }}</button></div>
      <div class="overflow-x-auto"><table class="w-full text-left text-sm"><thead class="text-text-dim"><tr><th scope="col" class="p-2">{{ t('musicLora.engine') }}</th><th scope="col" class="p-2">{{ t('musicLora.loading') }}</th><th scope="col" class="p-2">{{ t('musicLora.training') }}</th><th scope="col" class="p-2"><span class="sr-only">{{ t('musicLora.capability') }}</span></th></tr></thead><tbody><tr v-for="engine in modelCapabilities" :key="engine.engine" :data-engine="engine.engine" class="border-t border-border"><th scope="row" class="p-2 font-medium text-text">{{ t(`musicLora.engines.${engine.engine}`) }}</th><td class="p-2 text-text-dim">{{ t(engine.loading ? 'musicLora.supported' : 'musicLora.unsupported') }}</td><td class="p-2 text-text-dim">{{ t(engine.training ? 'musicLora.supported' : 'musicLora.unsupported') }}</td><td class="p-2 text-xs text-text-dim">{{ t(`musicLora.reasons.${engine.reason}`) }}</td></tr></tbody></table></div>
    </section>

    <form v-if="props.engine === 'stable_audio3'" data-training :aria-label="t('musicLora.stableTraining')" class="space-y-6 rounded-xl border border-border bg-panel p-5" @submit.prevent="start">
      <fieldset class="min-w-0 space-y-4">
        <legend class="mb-3 font-semibold text-text">{{ t('musicLora.simple.nameStep') }}</legend>
        <div class="grid gap-4 sm:grid-cols-2">
          <label class="lora-label">{{ t('musicLora.name') }}<input v-model="name" name="training-name" maxlength="120" required class="lora-input" :disabled="busy" :aria-invalid="trainingNameError ? true : undefined" :aria-describedby="trainingNameError ? 'training-name-error' : undefined" @input="nameError && nameArea === 'training' && (error = '')" /><span class="text-xs">{{ t('musicLora.nameUnique') }}</span><span v-if="trainingNameError" id="training-name-error" data-training-name-error role="alert" class="text-status-failed">{{ trainingNameError }}</span></label>
          <label class="lora-label">{{ t('musicLora.simple.baseModel') }}<select v-model="variant" name="training-variant" class="lora-input" :disabled="busy"><option value="small-music">{{ t('musicLora.targets.small-music') }}</option><option value="medium">{{ t('musicLora.targets.medium') }}</option></select><span class="text-xs leading-relaxed">{{ t('musicLora.simple.baseHint') }}</span></label>
        </div>
      </fieldset>
      <fieldset class="min-w-0 space-y-3 border-t border-border pt-4">
        <legend class="mb-2 pr-3 font-semibold text-text">{{ t('musicLora.simple.tracksStep') }}</legend>
        <p class="text-sm text-text-dim">{{ t('musicLora.simple.captionHint') }}</p>
        <p class="rounded-lg border border-accent1/30 bg-accent1/10 px-3 py-2 text-sm font-semibold text-text">{{ t('musicLora.simple.trackCount', { count: samples.length }) }}</p>
        <div v-for="sample in samples" :key="trainingSampleKey(sample)" class="space-y-2 rounded-lg border border-border bg-panel-2/50 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2"><div class="min-w-0 space-y-1"><h4 class="break-words text-sm font-medium text-text">{{ sample.title }}</h4><span class="inline-flex rounded-full border border-status-done/30 bg-status-done/10 px-2 py-1 text-xs font-medium text-status-done">{{ t('musicLora.selection.selected') }}</span></div><button type="button" class="lora-button" :disabled="busy" @click="removeSample(sample)">{{ t('musicLora.remove') }}</button></div>
          <label class="lora-label">{{ t('musicLora.caption') }}<textarea v-model="sample.caption" :name="`caption-${trainingSampleKey(sample)}`" :placeholder="t('musicLora.simple.captionExample')" maxlength="4000" rows="2" required class="lora-input resize-y" :disabled="busy" /></label>
        </div>
        <button type="button" class="lora-button" :disabled="busy || samples.length >= 100" @click="pickerOpen = true">{{ t('musicLora.addSample') }}</button>
        <TrainingDatasetPicker :disabled="busy" :limit="100 - samples.length" @pick="addDataset" />
        <p data-training-selection-status role="status" aria-atomic="true" class="text-sm font-medium text-status-done">{{ selectionMessage }}</p>
        <p class="text-xs leading-relaxed text-text-dim">{{ t('musicLora.simple.audioNote') }}</p>
      </fieldset>
      <fieldset class="min-w-0 space-y-4 border-t border-border pt-4">
        <legend class="mb-2 pr-3 font-semibold text-text"><span class="inline-flex items-center gap-1">{{ t('musicLora.simple.trainStep') }}<TrainingOptionsHelp engine="stable_audio3" /></span></legend>
        <CollapsibleDetails :summary="t('musicLora.simple.advanced')">
          <p class="text-xs text-text-dim">{{ t('musicLora.simple.settingsHint') }}</p>
          <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <label class="lora-label">{{ t('musicLora.steps') }}<input v-model.number="maxSteps" name="training-steps" type="number" min="1" max="100000" step="1" required class="lora-input" :disabled="busy" /></label>
            <label class="lora-label">{{ t('musicLora.rank') }}<select v-model.number="rank" name="training-rank" class="lora-input" :disabled="busy"><option v-for="value in [8, 16, 32]" :key="value" :value="value">{{ value }}</option></select></label>
            <label class="lora-label">{{ t('musicLora.learningRate') }}<input v-model.number="learningRate" name="training-learning-rate" type="number" min="0.0000001" max="0.01" step="any" required class="lora-input" :disabled="busy" /></label>
            <label class="lora-label">{{ t('musicLora.seed') }}<input v-model.number="seed" name="training-seed" type="number" min="0" max="2147483647" step="1" required class="lora-input" :disabled="busy" /></label>
          </div>
        </CollapsibleDetails>
        <p class="text-sm text-text-dim">{{ t('musicLora.simple.stepsSummary', { steps: maxSteps }) }}</p>
        <p v-if="trainingRequirement" id="stable-training-requirement" data-training-requirement role="status" class="text-sm text-text-dim">{{ trainingRequirement }}</p>
        <button type="submit" class="lora-button accent-gradient text-white" :disabled="busy || !canTrain" :aria-describedby="trainingRequirement ? 'stable-training-requirement' : undefined">{{ t('musicLora.start') }}</button>
        <p class="text-xs leading-relaxed text-text-dim">{{ t('musicLora.simple.runNote') }}</p>
      </fieldset>
    </form>

    <section v-if="props.engine === 'stable_audio3'" class="space-y-4">
      <h3 class="font-semibold text-text">{{ t('musicLora.trainingHistory') }}</h3>
      <p v-if="!jobs.length" class="text-sm text-text-dim">{{ t('musicLora.emptyTraining') }}</p>
      <article v-for="job in jobs" :key="job.id" class="space-y-3 rounded-xl border border-border bg-panel p-5">
        <div class="flex flex-wrap items-start justify-between gap-3"><div><h4 class="break-words font-medium text-text">{{ job.request.name }}</h4><p role="status" class="mt-1 text-xs text-text-dim">{{ t(`musicLora.states.${job.status}`) }}</p></div><button v-if="ongoing(job)" type="button" class="lora-button" :disabled="busy" @click="cancel(job)">{{ t('musicLora.cancel') }}</button></div>
        <TrainingJobProgress :job="job" :reconnecting="reconnecting" />
        <p v-if="job.status === 'failed'" role="alert" class="text-sm text-status-failed">{{ errorText(job.error_code, 'musicLora.errors.failed') }}</p>
        <a v-if="job.adapter" :href="musicAdapterFileUrl(job.adapter.id)" :download="`${job.adapter.name}.safetensors`" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.download') }}</a><div v-if="job.adapter" class="flex flex-wrap gap-4 text-sm"><RouterLink to="/music/stable-audio3" class="inline-flex min-h-11 items-center text-accent1 underline" @click="preview(job, true)">{{ t('musicLora.modelTraining.preview') }}</RouterLink><RouterLink to="/music/stable-audio3" class="inline-flex min-h-11 items-center text-accent1 underline" @click="preview(job, false)">{{ t('musicLora.modelTraining.compare') }}</RouterLink></div><p v-if="job.adapter" class="text-xs text-text-dim">{{ t('musicLora.modelTraining.comparisonHint') }}</p>
      </article>
    </section>

    <CollapsibleDetails :summary="t('musicLora.simple.existingAdapters')" :open="props.engine !== 'stable_audio3'">
      <form v-if="modelCapabilities.some(value => value.loading)" class="space-y-4 rounded-xl border border-border bg-panel p-4" @submit.prevent="importAdapter">
        <h3 class="font-semibold text-text">{{ t('musicLora.import') }}</h3><p class="text-xs leading-relaxed text-text-dim">{{ t('musicLora.formatHint') }}</p>
        <label class="lora-label">{{ t('musicLora.target') }}<select v-model="importTarget" name="import-target" class="lora-input" :disabled="busy"><option v-for="target in importTargets" :key="target" :value="target">{{ t(`musicLora.targets.${target}`) }}</option></select></label>
        <label class="lora-label">{{ t('musicLora.name') }}<input v-model="importName" name="import-name" maxlength="120" required class="lora-input" :disabled="busy" :aria-invalid="importNameError ? true : undefined" :aria-describedby="importNameError ? 'import-name-error' : undefined" @input="nameError && nameArea === 'import' && (error = '')" /><span class="text-xs">{{ t('musicLora.nameUnique') }}</span><span v-if="importNameError" id="import-name-error" data-import-name-error role="alert" class="text-status-failed">{{ importNameError }}</span></label>
        <label class="lora-label">{{ t('musicLora.file') }}<input ref="fileInput" type="file" accept=".safetensors" required class="lora-input" :disabled="busy" @change="fileChanged" /></label>
        <button type="submit" class="lora-button" :disabled="busy || !canImport">{{ t('musicLora.importAction') }}</button>
      </form>
      <section class="space-y-3 rounded-xl border border-border bg-panel p-4">
        <h3 class="font-semibold text-text">{{ t('musicLora.history') }}</h3><p v-if="!modelAdapters.length" class="text-sm text-text-dim">{{ t('musicLora.empty') }}</p>
        <ul class="space-y-3"><li v-for="adapter in modelAdapters" :key="adapter.id" class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3"><div><h4 class="break-words text-sm font-medium text-text">{{ adapter.name }}</h4><p class="text-xs text-text-dim">{{ t(`musicLora.engines.${adapter.engine}`) }} · {{ t(`musicLora.targets.${adapter.target}`) }}</p></div><a :href="musicAdapterFileUrl(adapter.id)" :download="`${adapter.name}.safetensors`" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.download') }}</a></li></ul>
      </section>
    </CollapsibleDetails>
    <LibraryPicker v-if="pickerOpen" track-only :selected-track-ids="selectedTrackIds" @pick="pickTrack" @close="pickerOpen = false" />
  </div>
</template>
<style scoped>
@reference "../../style.css";
.lora-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.lora-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.lora-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
