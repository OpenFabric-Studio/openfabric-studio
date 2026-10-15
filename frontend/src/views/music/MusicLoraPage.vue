<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import * as library from '../../api/musicLora'
import { musicAdapterFileUrl } from '../../api/musicLora'
import * as training from '../../api/stableLora'
import { ApiError } from '../../api/http'
import type { MusicAdapter, MusicLoraCapabilities, StableLoraSample, StableLoraTrainingJob, StableLoraTrainingRequest, StableLoraTrainingStatus } from '../../api/generated'
import { createPollingLoop } from '../../composables/polling'
import LibraryPicker from '../../components/editor/LibraryPicker.vue'

const { t } = useI18n()
const capabilities = ref<MusicLoraCapabilities['engines']>([]), adapters = ref<MusicAdapter[]>([])
const status = ref<StableLoraTrainingStatus | null>(null), jobs = ref<StableLoraTrainingJob[]>([])
const adapterHistoryIncomplete = ref(false), trainingHistoryIncomplete = ref(false), busy = ref(false), error = ref('')
const importEngine = ref<MusicAdapter['engine']>('stable_audio3'), importTarget = ref<MusicAdapter['target']>('small-music'), importName = ref(''), importFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const importTargets = computed<MusicAdapter['target'][]>(() => importEngine.value === 'stable_audio3' ? ['small-music', 'medium'] : ['ar', 'nar'])
const canImport = computed(() => !!importFile.value && /\.safetensors$/i.test(importFile.value.name) && importFile.value.size > 8 && importFile.value.size <= 512 * 1024 * 1024 && importName.value.trim().length > 0 && importName.value.length <= 120)
const name = ref(''), variant = ref<NonNullable<StableLoraTrainingRequest['variant']>>('small-music')
const samples = ref<Array<StableLoraSample & { title: string }>>([]), pickerOpen = ref(false)
const maxSteps = ref(1000), rank = ref<NonNullable<StableLoraTrainingRequest['rank']>>(16), learningRate = ref(0.0001), seed = ref(42)
const active = computed(() => jobs.value.some(ongoing))
const weightsReady = computed(() => variant.value === 'small-music' ? status.value?.small_weights_ready : status.value?.medium_weights_ready)
const ready = computed(() => status.value?.supported && status.value.installed && weightsReady.value && capabilities.value.some(engine => engine.engine === 'stable_audio3' && engine.training))
const canTrain = computed(() => ready.value && !active.value && name.value.trim().length > 0 && name.value.length <= 120 && samples.value.length > 0 && samples.value.length <= 100
  && samples.value.every(sample => sample.caption.trim().length > 0 && sample.caption.length <= 4000)
  && Number.isInteger(maxSteps.value) && maxSteps.value >= 1 && maxSteps.value <= 100000
  && Number.isFinite(learningRate.value) && learningRate.value >= 0.0000001 && learningRate.value <= 0.01
  && Number.isInteger(seed.value) && seed.value >= 0 && seed.value <= 2147483647)
let alive = true, generation = 0, controller: AbortController | undefined
function ongoing(job: StableLoraTrainingJob): boolean { return job.status === 'queued' || job.status === 'running' }
function errorText(code: string | null | undefined, fallback: string): string {
  switch (code) {
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
    jobs.value = response.jobs; trainingHistoryIncomplete.value = response.history_incomplete ?? false
  } catch { if (alive && context.isCurrent()) error.value = t('musicLora.errors.load') }
  return active.value
}, 2500)
async function action(work: (signal: AbortSignal, isCurrent: () => boolean) => Promise<void>, fallback: string) {
  if (!alive || busy.value) return
  poll.stop(); const token = ++generation, request = new AbortController()
  const isCurrent = () => alive && token === generation && !request.signal.aborted
  controller = request; busy.value = true; error.value = ''
  try { await work(request.signal, isCurrent) }
  catch (cause) { if (isCurrent()) error.value = errorText(cause instanceof ApiError ? cause.message : null, fallback) }
  finally { if (isCurrent()) { busy.value = false; controller = undefined; if (active.value) poll.start(false) } }
}
function refresh() {
  return action(async (signal, isCurrent) => {
    const results = await Promise.allSettled([library.musicLoraCapabilities(signal), library.listMusicAdapters(signal), training.stableLoraStatus(signal), training.listStableLora(signal)])
    if (!isCurrent()) return
    const [capability, registry, setup, history] = results
    if (capability.status === 'fulfilled') capabilities.value = capability.value.engines
    if (registry.status === 'fulfilled') { adapters.value = registry.value.adapters; adapterHistoryIncomplete.value = registry.value.history_incomplete ?? false }
    if (setup.status === 'fulfilled') status.value = setup.value
    if (history.status === 'fulfilled') { jobs.value = history.value.jobs; trainingHistoryIncomplete.value = history.value.history_incomplete ?? false }
    if (results.some(result => result.status === 'rejected')) error.value = t('musicLora.errors.load')
  }, 'musicLora.errors.load')
}
function fileChanged(event: Event) {
  const target = event.target
  if (target instanceof HTMLInputElement) importFile.value = target.files?.[0] ?? null
}
function importAdapter() {
  if (!canImport.value || !importFile.value) return
  const file = importFile.value, metadata = { engine: importEngine.value, target: importTarget.value, name: importName.value.trim() }
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
}
function start() {
  if (!canTrain.value) return
  const request: StableLoraTrainingRequest = { name: name.value.trim(), variant: variant.value, samples: samples.value.map(sample => ({ track_id: sample.track_id, caption: sample.caption.trim() })), max_steps: maxSteps.value, rank: rank.value, learning_rate: learningRate.value, seed: seed.value }
  return action(async (signal, isCurrent) => {
    const response = await training.submitStableLora(request, signal)
    if (isCurrent()) jobs.value = [response, ...jobs.value.filter(job => job.id !== response.id)]
  }, 'musicLora.errors.training')
}
function cancel(job: StableLoraTrainingJob) {
  return action(async (signal, isCurrent) => {
    const response = await training.cancelStableLora(job.id, signal)
    if (isCurrent()) jobs.value = jobs.value.map(existing => existing.id === response.id ? response : existing)
  }, 'musicLora.errors.training')
}
watch(importEngine, engine => { importTarget.value = engine === 'stable_audio3' ? 'small-music' : 'ar' })
onMounted(() => { void refresh() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <div class="min-w-0 space-y-5" :aria-busy="busy">
    <header class="space-y-2"><h2 class="text-xl font-semibold text-text">{{ t('musicLora.title') }}</h2><p class="text-sm text-text-dim">{{ t('musicLora.intro') }}</p></header>
    <p v-if="busy" role="status" class="text-sm text-text-dim">{{ t('musicLora.working') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="adapterHistoryIncomplete || trainingHistoryIncomplete" role="alert" class="text-sm text-status-queued">{{ t('musicLora.historyIncomplete') }}</p>
    <section class="space-y-3 rounded-xl border border-border bg-panel p-5">
      <div class="flex flex-wrap items-center justify-between gap-3"><h3 class="font-semibold text-text">{{ t('musicLora.capability') }}</h3><button type="button" class="lora-button" :disabled="busy" @click="refresh">{{ t('musicLora.refresh') }}</button></div>
      <div class="overflow-x-auto"><table class="w-full text-left text-sm"><thead class="text-text-dim"><tr><th scope="col" class="p-2">{{ t('musicLora.engine') }}</th><th scope="col" class="p-2">{{ t('musicLora.loading') }}</th><th scope="col" class="p-2">{{ t('musicLora.training') }}</th><th scope="col" class="p-2"><span class="sr-only">{{ t('musicLora.capability') }}</span></th></tr></thead><tbody><tr v-for="engine in capabilities" :key="engine.engine" :data-engine="engine.engine" class="border-t border-border"><th scope="row" class="p-2 font-medium text-text">{{ t(`musicLora.engines.${engine.engine}`) }}</th><td class="p-2 text-text-dim">{{ t(engine.loading ? 'musicLora.supported' : 'musicLora.unsupported') }}</td><td class="p-2 text-text-dim">{{ t(engine.training ? 'musicLora.supported' : 'musicLora.unsupported') }}</td><td class="p-2 text-xs text-text-dim">{{ t(`musicLora.reasons.${engine.reason}`) }}</td></tr></tbody></table></div>
      <RouterLink to="/ace-step/lora" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.ace') }}</RouterLink><p class="text-xs text-text-dim">{{ t('musicLora.aceHint') }}</p>
    </section>
    <form class="space-y-4 rounded-xl border border-border bg-panel p-5" @submit.prevent="importAdapter">
      <h3 class="font-semibold text-text">{{ t('musicLora.import') }}</h3><p class="text-xs leading-relaxed text-text-dim">{{ t('musicLora.formatHint') }}</p>
      <div class="grid gap-4 sm:grid-cols-2"><label class="lora-label">{{ t('musicLora.engine') }}<select v-model="importEngine" name="import-engine" class="lora-input" :disabled="busy"><option value="stable_audio3">{{ t('musicLora.engines.stable_audio3') }}</option><option value="yue2">{{ t('musicLora.engines.yue2') }}</option></select></label><label class="lora-label">{{ t('musicLora.target') }}<select v-model="importTarget" name="import-target" class="lora-input" :disabled="busy"><option v-for="target in importTargets" :key="target" :value="target">{{ t(`musicLora.targets.${target}`) }}</option></select></label></div>
      <label class="lora-label">{{ t('musicLora.name') }}<input v-model="importName" name="import-name" maxlength="120" required class="lora-input" :disabled="busy" /></label>
      <label class="lora-label">{{ t('musicLora.file') }}<input ref="fileInput" type="file" accept=".safetensors" required class="lora-input" :disabled="busy" @change="fileChanged" /></label>
      <button type="submit" class="lora-button" :disabled="busy || !canImport">{{ t('musicLora.importAction') }}</button>
    </form>
    <section class="space-y-3 rounded-xl border border-border bg-panel p-5"><h3 class="font-semibold text-text">{{ t('musicLora.history') }}</h3><p v-if="!adapters.length" class="text-sm text-text-dim">{{ t('musicLora.empty') }}</p><ul class="space-y-3"><li v-for="adapter in adapters" :key="adapter.id" class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3"><div><h4 class="break-words text-sm font-medium text-text">{{ adapter.name }}</h4><p class="text-xs text-text-dim">{{ t(`musicLora.engines.${adapter.engine}`) }} · {{ t(`musicLora.targets.${adapter.target}`) }}</p></div><a :href="musicAdapterFileUrl(adapter.id)" :download="`${adapter.name}.safetensors`" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.download') }}</a></li></ul></section>
    <form data-training class="space-y-4 rounded-xl border border-border bg-panel p-5" @submit.prevent="start">
      <h3 class="font-semibold text-text">{{ t('musicLora.stableTraining') }}</h3><p class="text-xs leading-relaxed text-text-dim">{{ t('musicLora.fresh') }}</p>
      <RouterLink to="/settings#module-stable_audio3" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.setup') }}</RouterLink>
      <p v-if="status && !status.supported" class="text-sm text-status-queued">{{ t('musicLora.unsupportedPlatform') }}</p><p v-else-if="status && !status.installed" class="text-sm text-status-queued">{{ t('musicLora.missingRuntime') }}</p><p v-else-if="status && !weightsReady" class="text-sm text-status-queued">{{ t('musicLora.missingWeights') }}</p>
      <div class="grid gap-4 sm:grid-cols-2"><label class="lora-label">{{ t('musicLora.name') }}<input v-model="name" name="training-name" maxlength="120" required class="lora-input" :disabled="busy" /></label><label class="lora-label">{{ t('musicLora.target') }}<select v-model="variant" name="training-variant" class="lora-input" :disabled="busy"><option value="small-music">{{ t('musicLora.targets.small-music') }}</option><option value="medium">{{ t('musicLora.targets.medium') }}</option></select></label></div>
      <fieldset class="space-y-3"><legend class="text-sm font-medium text-text">{{ t('musicLora.samples') }}</legend><p v-if="!samples.length" class="text-xs text-text-dim">{{ t('musicLora.emptySamples') }}</p><div v-for="sample in samples" :key="sample.track_id" class="space-y-2 rounded-lg border border-border p-3"><div class="flex flex-wrap items-center justify-between gap-2"><h4 class="break-words text-sm text-text">{{ sample.title }}</h4><button type="button" class="lora-button" :disabled="busy" @click="samples = samples.filter(item => item.track_id !== sample.track_id)">{{ t('musicLora.remove') }}</button></div><label class="lora-label">{{ t('musicLora.caption') }}<textarea v-model="sample.caption" :name="`caption-${sample.track_id}`" maxlength="4000" rows="2" required class="lora-input resize-y" :disabled="busy" /></label></div><button type="button" class="lora-button" :disabled="busy || samples.length >= 100" @click="pickerOpen = true">{{ t('musicLora.addSample') }}</button></fieldset>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><label class="lora-label">{{ t('musicLora.steps') }}<input v-model.number="maxSteps" name="training-steps" type="number" min="1" max="100000" step="1" required class="lora-input" :disabled="busy" /></label><label class="lora-label">{{ t('musicLora.rank') }}<select v-model.number="rank" name="training-rank" class="lora-input" :disabled="busy"><option v-for="value in [8, 16, 32]" :key="value" :value="value">{{ value }}</option></select></label><label class="lora-label">{{ t('musicLora.learningRate') }}<input v-model.number="learningRate" name="training-learning-rate" type="number" min="0.0000001" max="0.01" step="any" required class="lora-input" :disabled="busy" /></label><label class="lora-label">{{ t('musicLora.seed') }}<input v-model.number="seed" name="training-seed" type="number" min="0" max="2147483647" step="1" required class="lora-input" :disabled="busy" /></label></div>
      <button type="submit" class="lora-button accent-gradient text-white" :disabled="busy || !canTrain">{{ t('musicLora.start') }}</button>
    </form>
    <section class="space-y-4"><h3 class="font-semibold text-text">{{ t('musicLora.trainingHistory') }}</h3><p v-if="!jobs.length" class="text-sm text-text-dim">{{ t('musicLora.emptyTraining') }}</p><article v-for="job in jobs" :key="job.id" class="space-y-3 rounded-xl border border-border bg-panel p-5"><div class="flex flex-wrap items-start justify-between gap-3"><div><h4 class="break-words font-medium text-text">{{ job.request.name }}</h4><p role="status" class="mt-1 text-xs text-text-dim">{{ t(`musicLora.states.${job.status}`) }} · {{ t(`musicLora.stages.${job.stage ?? 'preparing'}`) }}</p></div><button v-if="ongoing(job)" type="button" class="lora-button" :disabled="busy" @click="cancel(job)">{{ t('musicLora.cancel') }}</button></div><p v-if="job.status === 'failed'" role="alert" class="text-sm text-status-failed">{{ errorText(job.error_code, 'musicLora.errors.failed') }}</p><a v-if="job.adapter" :href="musicAdapterFileUrl(job.adapter.id)" :download="`${job.adapter.name}.safetensors`" class="inline-flex min-h-11 items-center text-sm text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicLora.download') }}</a></article></section>
    <LibraryPicker v-if="pickerOpen" track-only @pick="pickTrack" @close="pickerOpen = false" />
  </div>
</template>
<style scoped>
@reference "../../style.css";
.lora-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.lora-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.lora-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
