<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { pendingMusicAdapter } from '../../composables/generationDrafts'
import * as api from '../../api/musicTraining'
import { musicTrainingPackageUrl } from '../../api/musicTraining'
import { ApiError } from '../../api/http'
import type { MusicAdapter, MusicLoraTrainingJob, MusicLoraTrainingRequest, MusicLoraTrainingSample, MusicLoraTrainingStatus } from '../../api/generated'
import { musicAdapterFileUrl, musicLoraNameKey } from '../../api/musicLora'
import { createPollingLoop } from '../../composables/polling'
import LibraryPicker from '../../components/editor/LibraryPicker.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import HelpModal from '../../components/shared/HelpModal.vue'
import MusicTrainingSetup from './MusicTrainingSetup.vue'
import TrainingDatasetPicker from './TrainingDatasetPicker.vue'
import TrainingAudioImport from './TrainingAudioImport.vue'
import TrainingCaptionAnalyzer from './TrainingCaptionAnalyzer.vue'
import TrainingOptionsHelp from './TrainingOptionsHelp.vue'
import TrainingJobProgress from './TrainingJobProgress.vue'
import { savedTrainingSamples, trainingSampleKey } from '../../composables/musicTrainingSamples'
import type { LoraDatasetOperationResponse, MusicLoraSource } from '../../api/generated'

const props = defineProps<{ engine: MusicLoraTrainingRequest['engine']; managedNames?: string[] }>()
const emit = defineEmits<{ registered: []; historyNames: [names: string[]] }>()
const { t } = useI18n()
const status = ref<MusicLoraTrainingStatus | null>(null), jobs = ref<MusicLoraTrainingJob[]>([])
const historyKnown = ref(false), reconnecting = ref(false), incomplete = ref(false), busy = ref(false), error = ref('')
const name = ref(''), samples = ref<Array<MusicLoraTrainingSample & { title: string }>>([]), pickerOpen = ref(false)
const importing = ref(false), captioning = ref(false)
const maxSteps = ref(1000), rank = ref<NonNullable<MusicLoraTrainingRequest['rank']>>(16), learningRate = ref(0.0001), seed = ref(42), seconds = ref(12)
const plannerSteps = ref(100), plannerLearningRate = ref(0.00005)
const plannerSource = ref<MusicLoraTrainingJob | null>(null), missingPlannerSteps = ref(100), missingPlannerRate = ref(0.00005)
const modelJobs = computed(() => jobs.value.filter(job => job.request.engine === props.engine))
const nameTaken = computed(() => !!musicLoraNameKey(name.value) && [...modelJobs.value.map(job => job.request.name), ...(props.managedNames ?? [])].some(value => musicLoraNameKey(value) === musicLoraNameKey(name.value)))
const nameError = computed(() => nameTaken.value ? t('musicLora.errors.nameExists') : [t('musicLora.errors.nameExists'), t('musicLora.errors.nameUnavailable')].includes(error.value) ? error.value : '')
const selectedTrackIds = computed(() => samples.value.flatMap(sample => sample.track_id == null ? [] : [sample.track_id]))
const active = computed(() => jobs.value.some(job => job.status === 'running' || job.status === 'queued'))
const trainingReady = computed(() => !!status.value?.supported && status.value.installed && status.value.weights_ready)
const ready = computed(() => trainingReady.value && status.value?.paired_ready === true)
const setupMode = computed(() => trainingReady.value && !ready.value ? 'scores' : 'training')
const canSubmit = computed(() => historyKnown.value && !active.value && !busy.value && !importing.value && !captioning.value && !nameTaken.value && !!name.value.trim() && name.value.length <= 120 && samples.value.length > 0 && samples.value.length <= 100
  && samples.value.every(sample => !!sample.caption.trim() && sample.caption.length <= 4000 && (sample.lyrics?.length ?? 0) <= 20000)
  && Number.isInteger(plannerSteps.value) && plannerSteps.value >= 1 && plannerSteps.value <= 100000 && Number.isFinite(plannerLearningRate.value) && plannerLearningRate.value >= 1e-7 && plannerLearningRate.value <= 0.01
  && Number.isInteger(maxSteps.value) && maxSteps.value >= 1 && maxSteps.value <= 100000 && Number.isInteger(seed.value) && seed.value >= 0 && seed.value <= 2147483647
  && Number.isInteger(seconds.value) && seconds.value >= 6 && seconds.value <= 30 && Number.isFinite(learningRate.value) && learningRate.value >= 1e-7 && learningRate.value <= 0.01)
const canTrain = computed(() => ready.value && canSubmit.value)
const canStartPlanner = computed(() => plannerSource.value?.can_train_missing_ar === true && historyKnown.value && !reconnecting.value && !incomplete.value && !active.value && !busy.value && !importing.value && !captioning.value && ready.value
  && modelJobs.value.some(job => job.id === plannerSource.value?.id && job.can_train_missing_ar === true)
  && Number.isInteger(missingPlannerSteps.value) && missingPlannerSteps.value >= 1 && missingPlannerSteps.value <= 100000 && Number.isFinite(missingPlannerRate.value) && missingPlannerRate.value >= 1e-7 && missingPlannerRate.value <= 0.01)
let alive = true, generation = 0, controller: AbortController | undefined
const completed = new Set<string>()
function accept(response: { jobs: MusicLoraTrainingJob[]; history_incomplete?: boolean }) {
  jobs.value = response.jobs; historyKnown.value = true; reconnecting.value = false; incomplete.value = response.history_incomplete ?? false
  emit('historyNames', modelJobs.value.map(job => job.request.name))
  for (const job of response.jobs) if (job.kind !== 'nvidia-package' && job.status === 'done' && !completed.has(job.id)) { completed.add(job.id); emit('registered') }
}
function safeError(cause: unknown): string {
  const code = cause instanceof ApiError ? cause.message : ''
  if (code === 'music_lora_name_exists') return t('musicLora.errors.nameExists')
  if (code === 'music_lora_name_unavailable') return t('musicLora.errors.nameUnavailable')
  if (code === 'music_training_planner_exists') return t('musicLora.missingAr.exists')
  if (code === 'music_training_planner_source_unavailable' || code === 'music_training_planner_request_invalid') return t('musicLora.missingAr.sourceUnavailable')
  return t(['music_training_busy', 'module_setup_busy', 'music_queue_busy'].includes(code) ? 'musicLora.errors.busy' : code === 'music_training_dataset_invalid' ? 'musicLora.errors.track' : 'musicLora.modelTraining.error')
}
const poll = createPollingLoop(async context => {
  try { const response = await api.listMusicTraining(context.signal); if (alive && context.isCurrent()) accept(response) }
  catch { if (alive && context.isCurrent()) reconnecting.value = true }
  return active.value || !historyKnown.value || reconnecting.value
}, 2500)
async function action(work: (signal: AbortSignal, current: () => boolean) => Promise<void>) {
  if (!alive || busy.value) return
  poll.stop(); const request = new AbortController(), token = ++generation
  const current = () => alive && generation === token && !request.signal.aborted
  controller = request; busy.value = true; error.value = ''
  try { await work(request.signal, current) }
  catch (cause) { if (current()) error.value = safeError(cause) }
  finally { if (current()) { busy.value = false; controller = undefined; if (active.value || !historyKnown.value || reconnecting.value) poll.start(false) } }
}
function refresh() {
  return action(async (signal, current) => {
    const results = await Promise.allSettled([api.musicTrainingStatus(signal), api.listMusicTraining(signal)])
    if (!current()) return
    const [setup, history] = results
    if (setup.status === 'fulfilled') status.value = setup.value.engines.find(item => item.engine === props.engine) ?? null
    else { status.value = null; error.value = t('musicLora.errors.load') }
    if (history.status === 'fulfilled') accept(history.value)
    else { historyKnown.value = false; reconnecting.value = true }
  })
}
function pick(track: { trackId?: number; sourceLabel: string }) {
  pickerOpen.value = false
  if (!track.trackId || !Number.isSafeInteger(track.trackId) || samples.value.length >= 100 || samples.value.some(sample => sample.track_id === track.trackId)) return
  samples.value.push({ track_id: track.trackId, caption: '', lyrics: '', instrumental: false, title: track.sourceLabel })
}
function addDataset(snapshot: LoraDatasetOperationResponse) {
  const selected = savedTrainingSamples(snapshot, props.engine, 100 - samples.value.length)
  const existing = new Set(samples.value.map(trainingSampleKey))
  samples.value.push(...selected.filter(sample => !existing.has(trainingSampleKey(sample))))
}
function applyCaption(value: { source: MusicLoraSource; caption: string }) {
  const source = value.source
  const sample = samples.value.find(item => source.dataset ? item.dataset?.dataset_id === source.dataset.dataset_id && item.dataset.revision === source.dataset.revision && item.dataset.sample_id === source.dataset.sample_id : !item.dataset && item.track_id === source.track_id)
  if (sample && !sample.caption.trim()) sample.caption = value.caption
}

function start(kind: 'training' | 'nvidia-package') {
  if (!(kind === 'training' ? canTrain.value : canSubmit.value)) return
  const request: MusicLoraTrainingRequest = { engine: props.engine, name: name.value.trim(), samples: samples.value.map(sample => ({ ...(sample.dataset ? { dataset: sample.dataset } : { track_id: sample.track_id }), caption: sample.caption.trim(), lyrics: sample.instrumental ? '' : sample.lyrics?.trim() ?? '', instrumental: sample.instrumental ?? false })), max_steps: maxSteps.value, rank: rank.value, learning_rate: learningRate.value, seed: seed.value, segment_seconds: seconds.value, yue_pair: { planner_steps: plannerSteps.value, planner_learning_rate: plannerLearningRate.value } }
  const submit = kind === 'training' ? api.submitMusicTraining : api.createMusicTrainingPackage
  return action(async (signal, current) => { historyKnown.value = false; const job = await submit(request, signal); if (current()) { jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id)]; historyKnown.value = true; emit('historyNames', modelJobs.value.map(item => item.request.name)) } })
}
function adapters(job: MusicLoraTrainingJob): MusicAdapter[] {
  return job.adapters?.length ? job.adapters : job.adapter ? [job.adapter] : []
}
function preview(job: MusicLoraTrainingJob, useAdapter: boolean) {
  if (job.kind === 'nvidia-package') return
  const saved = adapters(job)
  const ar = saved.find(adapter => adapter.engine === 'yue2' && adapter.target === 'ar')
  const nar = saved.find(adapter => adapter.engine === 'yue2' && adapter.target === 'nar')
  if (ar && nar) {
    const pair = { ar: useAdapter ? { id: ar.id, strength: 1 } : null, nar: useAdapter ? { id: nar.id, strength: 1 } : null }
    pendingMusicAdapter.value = { engine: 'yue2', target: 'nar', selection: pair.nar, pair }
    return
  }
  const adapter = job.adapter ?? saved[0]
  if (!adapter) return
  const selection = useAdapter ? { id: adapter.id, strength: 1 } : null
  pendingMusicAdapter.value = { engine: adapter.engine, target: adapter.target, selection }
}
function cancel(job: MusicLoraTrainingJob) {
  return action(async (signal, current) => { const response = await api.cancelMusicTraining(job.id, signal); if (current()) jobs.value = jobs.value.map(item => item.id === response.id ? response : item) })
}
function reviewPlanner(job: MusicLoraTrainingJob) {
  if (job.can_train_missing_ar !== true || !historyKnown.value || reconnecting.value || incomplete.value || active.value || busy.value) return
  missingPlannerSteps.value = 100; missingPlannerRate.value = 0.00005; plannerSource.value = job
}
function startPlanner() {
  const source = plannerSource.value
  if (!source || !canStartPlanner.value) return
  return action(async (signal, current) => {
    historyKnown.value = false
    const job = await api.trainMissingMusicPlanner(source.id, { planner_steps: missingPlannerSteps.value, planner_learning_rate: missingPlannerRate.value }, signal)
    if (!current()) return
    jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id).map(item => item.id === source.id ? { ...item, can_train_missing_ar: false } : item)]
    historyKnown.value = true; plannerSource.value = null
  })
}
onMounted(() => { void refresh() })
onDeactivated(() => { plannerSource.value = null })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <section data-model-training class="space-y-4" :aria-busy="busy">
    <div class="space-y-3 rounded-xl border border-border bg-panel p-5">
      <div class="flex flex-wrap items-center justify-between gap-3"><h3 class="font-semibold text-text">{{ t('musicLora.modelTraining.title') }}</h3><button type="button" class="training-button" :disabled="busy" @click="refresh">{{ t('musicLora.refresh') }}</button></div>
      <p class="text-sm text-text-dim">{{ t('musicLora.modelTraining.acoustic') }}</p>
      <p v-if="status" role="status" class="text-sm" :class="ready ? 'text-status-done' : 'text-status-queued'">{{ t(ready ? 'musicLora.simple.ready' : status.supported ? setupMode === 'scores' ? 'musicLora.scorePrepare.review' : 'musicLora.modelTraining.prepare' : 'musicLora.modelTraining.unsupported') }}</p>
      <p class="text-xs text-status-queued">{{ t('musicLora.modelTraining.experimental') }}</p>
      <MusicTrainingSetup v-if="status" :mode="setupMode" :engine="engine" :supported="status.supported" :installed="status.installed" :ready="ready" @refresh="refresh" />
    </div>
    <p v-if="error && !nameError && !plannerSource" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="reconnecting" role="status" class="text-sm text-status-queued">{{ t('loraWorkspace.reconnecting') }}</p>
    <p v-if="incomplete" role="alert" class="text-sm text-status-queued">{{ t('musicLora.historyIncomplete') }}</p>
    <form class="space-y-5 rounded-xl border border-border bg-panel p-5" @submit.prevent="start('training')">
      <fieldset class="space-y-3"><legend class="mb-3 font-semibold text-text">{{ t('musicLora.simple.nameStep') }}</legend><label class="training-label">{{ t('musicLora.name') }}<input v-model="name" name="model-training-name" maxlength="120" required class="training-input" :disabled="busy" :aria-invalid="nameError ? true : undefined" :aria-describedby="nameError ? 'model-training-name-error' : undefined" @input="nameError && (error = '')" /></label><p v-if="nameError" id="model-training-name-error" data-name-error role="alert" class="text-sm text-status-failed">{{ nameError }}</p><p class="text-xs text-text-dim">{{ t('musicLora.nameUnique') }}</p></fieldset>
      <fieldset class="space-y-3 border-t border-border pt-4">
        <legend class="pr-3 font-semibold text-text">{{ t('musicLora.simple.tracksStep') }}</legend>
        <p class="text-sm text-text-dim">{{ t('musicLora.modelTraining.excerpt', { seconds }) }}</p>
        <p class="text-sm text-text-dim">{{ t('musicLora.modelTraining.lyricsHint', { seconds }) }}</p>
        <TrainingAudioImport :disabled="busy" :limit="100 - samples.length" :selected-track-ids="selectedTrackIds" @pick="pick" @busy="importing = $event" />
        <TrainingCaptionAnalyzer :samples="samples" :segment-seconds="seconds" :disabled="busy || importing || active" @caption="applyCaption" @busy="captioning = $event" />
        <p role="status" class="text-sm text-text-dim">{{ t('musicLora.simple.trackCount', { count: samples.length }) }}</p>
        <div v-for="sample in samples" :key="trainingSampleKey(sample)" class="space-y-2 rounded-lg border border-border p-3">
          <div class="flex flex-wrap items-center justify-between gap-2"><h4 class="text-sm font-medium text-text">{{ sample.title }}</h4><button type="button" class="training-button" :disabled="busy" @click="samples = samples.filter(item => trainingSampleKey(item) !== trainingSampleKey(sample))">{{ t('musicLora.remove') }}</button></div>
          <label class="training-label">{{ t('musicLora.caption') }}<textarea v-model="sample.caption" :name="`model-caption-${trainingSampleKey(sample)}`" :placeholder="t('musicLora.simple.captionExample')" maxlength="4000" rows="2" required class="training-input" :disabled="busy" /></label>
          <label class="flex min-h-11 items-center gap-2 text-sm text-text-dim"><input v-model="sample.instrumental" :name="`model-instrumental-${trainingSampleKey(sample)}`" type="checkbox" :disabled="busy" @change="sample.instrumental && (sample.lyrics = '')" />{{ t('musicLora.modelTraining.instrumental') }}</label>
          <label v-if="!sample.instrumental" class="training-label">{{ t('musicLora.modelTraining.lyrics') }}<textarea v-model="sample.lyrics" :name="`model-lyrics-${trainingSampleKey(sample)}`" maxlength="20000" rows="3" class="training-input" :disabled="busy" /></label>
        </div>
        <button type="button" class="training-button" :disabled="busy || importing || samples.length >= 100" @click="pickerOpen = true">{{ t('musicLora.addSample') }}</button>
        <TrainingDatasetPicker :disabled="busy || importing" :limit="100 - samples.length" @pick="addDataset" />
      </fieldset>
      <fieldset class="space-y-4 border-t border-border pt-4"><legend class="pr-3 font-semibold text-text"><span class="inline-flex items-center gap-1">{{ t('musicLora.simple.trainStep') }}<TrainingOptionsHelp :engine="engine" /></span></legend>
        <CollapsibleDetails :summary="t('musicLora.simple.advanced')"><div class="space-y-4">
          <fieldset class="grid gap-4 sm:grid-cols-2"><legend class="mb-3 text-sm font-medium text-text">{{ t('musicLora.modelTraining.planner') }}</legend>
            <label class="training-label">{{ t('musicLora.steps') }}<input v-model.number="plannerSteps" name="model-training-planner-steps" type="number" min="1" max="100000" step="1" class="training-input" required :disabled="busy" /></label>
            <label class="training-label">{{ t('musicLora.learningRate') }}<input v-model.number="plannerLearningRate" name="model-training-planner-learning-rate" type="number" min="0.0000001" max="0.01" step="any" class="training-input" required :disabled="busy" /></label>
          </fieldset>
          <fieldset class="grid gap-4 sm:grid-cols-2"><legend class="mb-3 text-sm font-medium text-text">{{ t('musicLora.modelTraining.acousticTarget') }}</legend>
            <label class="training-label">{{ t('musicLora.steps') }}<input v-model.number="maxSteps" name="model-training-steps" type="number" min="1" max="100000" step="1" class="training-input" required :disabled="busy" /></label>
            <label class="training-label">{{ t('musicLora.learningRate') }}<input v-model.number="learningRate" name="model-training-learning-rate" type="number" min="0.0000001" max="0.01" step="any" class="training-input" required :disabled="busy" /></label>
          </fieldset>
          <fieldset class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"><legend class="mb-3 text-sm font-medium text-text">{{ t('musicLora.modelTraining.sharedSettings') }}</legend>
            <label class="training-label">{{ t('musicLora.rank') }}<select v-model.number="rank" name="model-training-rank" class="training-input" :disabled="busy"><option v-for="value in [8, 16, 32]" :key="value" :value="value">{{ value }}</option></select></label>
            <label class="training-label">{{ t('musicLora.modelTraining.seconds') }}<input v-model.number="seconds" name="model-training-seconds" type="number" min="6" max="30" step="1" class="training-input" required :disabled="busy" /></label>
            <label class="training-label">{{ t('musicLora.seed') }}<input v-model.number="seed" name="model-training-seed" type="number" min="0" max="2147483647" step="1" class="training-input" required :disabled="busy" /></label>
          </fieldset>
        </div></CollapsibleDetails>
        <p class="text-sm text-text-dim">{{ t('musicLora.modelTraining.pairSummary', { planner: plannerSteps, acoustic: maxSteps }) }}</p>
        <p class="text-xs text-text-dim">{{ t('musicLora.simple.runNote') }}</p>
        <button type="submit" class="training-button bg-accent1 text-white" :disabled="!canTrain">{{ t('musicLora.start') }}</button>
        <CollapsibleDetails data-nvidia-fallback :summary="t('musicLora.modelTraining.nvidia.title')">
          <p class="text-sm text-text-dim">{{ t('musicLora.modelTraining.nvidia.hint') }}</p>
          <p class="text-xs text-status-queued">{{ t('musicLora.modelTraining.nvidia.experimental') }}</p>
          <button type="button" class="training-button" :disabled="!canSubmit" @click="start('nvidia-package')">{{ t('musicLora.modelTraining.nvidia.create') }}</button>
        </CollapsibleDetails>
      </fieldset>
    </form>
    <section class="space-y-3" :aria-label="t('musicLora.modelTraining.history')">
      <h3 class="font-semibold text-text">{{ t('musicLora.modelTraining.history') }}</h3>
      <article v-for="job in modelJobs" :key="job.id" class="space-y-2 rounded-xl border border-border bg-panel p-4">
        <div class="flex flex-wrap justify-between gap-3"><h4 class="font-medium text-text">{{ job.request.name }}</h4><p role="status" class="text-sm text-text-dim">{{ t(job.kind === 'nvidia-package' && job.status === 'done' ? 'musicLora.modelTraining.nvidia.ready' : `musicLora.states.${job.status}`) }}</p></div>
        <TrainingJobProgress :job="job" :reconnecting="reconnecting" />
        <p v-if="job.kind === 'nvidia-package'" class="text-sm text-text-dim">{{ t('musicLora.modelTraining.nvidia.history') }}</p>
        <p v-if="job.error_code" role="alert" class="text-sm text-status-failed">{{ t(job.kind === 'nvidia-package' ? 'musicLora.modelTraining.nvidia.failed' : 'musicLora.modelTraining.failed') }}</p>
        <button v-if="job.status === 'running' || job.status === 'queued'" type="button" class="training-button" :disabled="busy" @click="cancel(job)">{{ t(job.kind === 'nvidia-package' ? 'musicLora.modelTraining.nvidia.cancel' : 'musicLora.cancel') }}</button>
        <button v-if="job.can_train_missing_ar === true" type="button" class="training-button" :disabled="busy || active || !historyKnown || reconnecting || incomplete || !ready" @click="reviewPlanner(job)">{{ t('musicLora.missingAr.open') }}</button>
        <a v-if="job.kind === 'nvidia-package' && job.status === 'done' && job.package_sha256" :href="musicTrainingPackageUrl(job.id)" download class="inline-flex min-h-11 items-center text-sm text-accent1 underline">{{ t('musicLora.modelTraining.nvidia.download') }}</a>
        <template v-if="job.kind !== 'nvidia-package' && adapters(job).length">
          <div class="flex flex-wrap gap-4"><a v-for="adapter in adapters(job)" :key="adapter.id" :href="musicAdapterFileUrl(adapter.id)" class="inline-flex min-h-11 items-center text-sm text-accent1 underline">{{ t('musicLora.download') }}<template v-if="adapters(job).length > 1"> · {{ adapter.name }} ({{ t(`musicLora.targets.${adapter.target}`) }})</template></a></div>
          <div class="flex flex-wrap gap-4 text-sm"><RouterLink to="/music/yue2" class="inline-flex min-h-11 items-center text-accent1 underline" @click="preview(job, true)">{{ t('musicLora.modelTraining.preview') }}</RouterLink><RouterLink to="/music/yue2" class="inline-flex min-h-11 items-center text-accent1 underline" @click="preview(job, false)">{{ t('musicLora.modelTraining.compare') }}</RouterLink></div>
          <p class="text-xs text-text-dim">{{ t('musicLora.modelTraining.comparisonHint') }}</p>
        </template>
      </article>
    </section>
    <LibraryPicker v-if="pickerOpen" :track-only="true" :selected-track-ids="selectedTrackIds" @close="pickerOpen = false" @pick="pick" />
    <HelpModal :open="plannerSource !== null" :title="t('musicLora.missingAr.open')" @close="plannerSource = null">
      <form v-if="plannerSource" class="space-y-4" @submit.prevent="startPlanner">
        <p>{{ t('musicLora.missingAr.hint') }}</p>
        <dl class="grid gap-3 sm:grid-cols-2">
          <div><dt>{{ t('musicLora.name') }}</dt><dd class="font-medium text-text">{{ plannerSource.request.name }}</dd></div>
          <div><dt>{{ t('musicLora.progress.recordings') }}</dt><dd class="text-text">{{ plannerSource.request.samples.length }}</dd></div>
          <div><dt>{{ t('musicLora.modelTraining.seconds') }}</dt><dd class="text-text">{{ plannerSource.request.segment_seconds ?? 12 }}</dd></div>
          <div><dt>{{ t('musicLora.rank') }}</dt><dd class="text-text">{{ plannerSource.request.rank ?? 16 }}</dd></div>
          <div><dt>{{ t('musicLora.seed') }}</dt><dd class="text-text">{{ plannerSource.request.seed ?? 42 }}</dd></div>
        </dl>
        <ul class="max-h-48 space-y-3 overflow-y-auto rounded-lg border border-border p-3"><li v-for="(sample, index) in plannerSource.request.samples" :key="index"><p class="font-medium text-text">{{ sample.track_id != null ? t('musicLora.progress.trackSource', { id: sample.track_id }) : t('musicLora.progress.datasetSource', { id: sample.dataset?.dataset_id, sample: sample.dataset?.sample_id }) }}</p><p class="whitespace-pre-wrap break-words">{{ sample.caption }}</p></li></ul>
        <label class="training-label">{{ t('musicLora.steps') }}<input v-model.number="missingPlannerSteps" name="missing-ar-steps" type="number" min="1" max="100000" step="1" required class="training-input" :disabled="busy" /></label>
        <label class="training-label">{{ t('musicLora.learningRate') }}<input v-model.number="missingPlannerRate" name="missing-ar-learning-rate" type="number" min="0.0000001" max="0.01" step="any" required class="training-input" :disabled="busy" /></label>
        <p v-if="error" role="alert" class="text-status-failed">{{ error }}</p>
        <p v-if="reconnecting || !historyKnown" role="status" class="text-status-queued">{{ t('musicLora.simple.checkHistory') }}</p>
        <button type="submit" class="training-button bg-accent1 text-white" :disabled="!canStartPlanner">{{ t('musicLora.missingAr.start') }}</button>
      </form>
    </HelpModal>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.training-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.training-input { @apply w-full min-w-0 rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text outline-none focus:border-accent1; }
.training-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
