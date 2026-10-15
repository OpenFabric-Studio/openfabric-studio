<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { pendingMusicAdapter } from '../../composables/generationDrafts'
import * as api from '../../api/musicTraining'
import { musicTrainingPackageUrl } from '../../api/musicTraining'
import { ApiError } from '../../api/http'
import type { MusicLoraTrainingJob, MusicLoraTrainingRequest, MusicLoraTrainingSample, MusicLoraTrainingStatus } from '../../api/generated'
import { musicAdapterFileUrl } from '../../api/musicLora'
import { createPollingLoop } from '../../composables/polling'
import LibraryPicker from '../../components/editor/LibraryPicker.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import MusicTrainingSetup from './MusicTrainingSetup.vue'
import TrainingDatasetPicker from './TrainingDatasetPicker.vue'
import TrainingOptionsHelp from './TrainingOptionsHelp.vue'
import TrainingJobProgress from './TrainingJobProgress.vue'
import { savedTrainingSamples, trainingSampleKey } from '../../composables/musicTrainingSamples'
import type { LoraDatasetOperationResponse } from '../../api/generated'

const props = defineProps<{ engine: 'heartmula' | 'yue2' }>()
const emit = defineEmits<{ registered: [] }>()
const { t } = useI18n()
const status = ref<MusicLoraTrainingStatus | null>(null), jobs = ref<MusicLoraTrainingJob[]>([])
const historyKnown = ref(false), reconnecting = ref(false), incomplete = ref(false), busy = ref(false), error = ref('')
const name = ref(''), samples = ref<Array<MusicLoraTrainingSample & { title: string }>>([]), pickerOpen = ref(false)
const maxSteps = ref(1000), rank = ref<NonNullable<MusicLoraTrainingRequest['rank']>>(16), learningRate = ref(0.0001), seed = ref(42), seconds = ref(12)
const modelJobs = computed(() => jobs.value.filter(job => job.request.engine === props.engine))
const selectedTrackIds = computed(() => samples.value.flatMap(sample => sample.track_id == null ? [] : [sample.track_id]))
const active = computed(() => jobs.value.some(job => job.status === 'running' || job.status === 'queued'))
const ready = computed(() => !!status.value?.supported && status.value.installed && status.value.weights_ready)
const canSubmit = computed(() => historyKnown.value && !active.value && !busy.value && !!name.value.trim() && name.value.length <= 120 && samples.value.length > 0 && samples.value.length <= 100
  && samples.value.every(sample => !!sample.caption.trim() && sample.caption.length <= 4000 && (sample.lyrics?.length ?? 0) <= 20000)
  && Number.isInteger(maxSteps.value) && maxSteps.value >= 1 && maxSteps.value <= 100000 && Number.isInteger(seed.value) && seed.value >= 0 && seed.value <= 2147483647
  && Number.isInteger(seconds.value) && seconds.value >= 6 && seconds.value <= 30 && Number.isFinite(learningRate.value) && learningRate.value >= 1e-7 && learningRate.value <= 0.01)
const canTrain = computed(() => ready.value && canSubmit.value)
let alive = true, generation = 0, controller: AbortController | undefined
const completed = new Set<string>()
function accept(response: { jobs: MusicLoraTrainingJob[]; history_incomplete?: boolean }) {
  jobs.value = response.jobs; historyKnown.value = true; reconnecting.value = false; incomplete.value = response.history_incomplete ?? false
  for (const job of response.jobs) if (job.kind !== 'nvidia-package' && job.status === 'done' && !completed.has(job.id)) { completed.add(job.id); emit('registered') }
}
function safeError(cause: unknown): string {
  const code = cause instanceof ApiError ? cause.message : ''
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

function start(kind: 'training' | 'nvidia-package') {
  if (!(kind === 'training' ? canTrain.value : canSubmit.value)) return
  const request: MusicLoraTrainingRequest = { engine: props.engine, name: name.value.trim(), samples: samples.value.map(sample => ({ ...(sample.dataset ? { dataset: sample.dataset } : { track_id: sample.track_id }), caption: sample.caption.trim(), lyrics: props.engine === 'heartmula' ? sample.lyrics?.trim() ?? '' : '', instrumental: sample.instrumental ?? false })), max_steps: maxSteps.value, rank: rank.value, learning_rate: learningRate.value, seed: seed.value, segment_seconds: seconds.value }
  const submit = kind === 'training' ? api.submitMusicTraining : api.createMusicTrainingPackage
  return action(async (signal, current) => { historyKnown.value = false; const job = await submit(request, signal); if (current()) { jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id)]; historyKnown.value = true } })
}
function preview(job: MusicLoraTrainingJob, useAdapter: boolean) {
  if (job.kind === 'nvidia-package' || !job.adapter) return
  const selection = useAdapter || job.adapter.engine === 'heartmula' ? { id: job.adapter.id, strength: useAdapter ? 1 : 0 } : null
  pendingMusicAdapter.value = { engine: job.adapter.engine, target: job.adapter.target, selection }
}
function cancel(job: MusicLoraTrainingJob) {
  return action(async (signal, current) => { const response = await api.cancelMusicTraining(job.id, signal); if (current()) jobs.value = jobs.value.map(item => item.id === response.id ? response : item) })
}
onMounted(() => { void refresh() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <section data-model-training class="space-y-4" :aria-busy="busy">
    <div class="space-y-3 rounded-xl border border-border bg-panel p-5">
      <div class="flex flex-wrap items-center justify-between gap-3"><h3 class="font-semibold text-text">{{ t('musicLora.modelTraining.title') }}</h3><button type="button" class="training-button" :disabled="busy" @click="refresh">{{ t('musicLora.refresh') }}</button></div>
      <p class="text-sm text-text-dim">{{ t(engine === 'yue2' ? 'musicLora.modelTraining.acoustic' : 'musicLora.modelTraining.heart') }}</p>
      <p v-if="status" role="status" class="text-sm" :class="ready ? 'text-status-done' : 'text-status-queued'">{{ t(ready ? 'musicLora.simple.ready' : status.supported ? 'musicLora.modelTraining.prepare' : 'musicLora.modelTraining.unsupported') }}</p>
      <p class="text-xs text-status-queued">{{ t('musicLora.modelTraining.experimental') }}</p>
      <MusicTrainingSetup v-if="status" :engine="engine" :supported="status.supported" :installed="status.installed" :ready="ready" @refresh="refresh" />
    </div>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="reconnecting" role="status" class="text-sm text-status-queued">{{ t('loraWorkspace.reconnecting') }}</p>
    <p v-if="incomplete" role="alert" class="text-sm text-status-queued">{{ t('musicLora.historyIncomplete') }}</p>
    <form class="space-y-5 rounded-xl border border-border bg-panel p-5" @submit.prevent="start('training')">
      <fieldset class="space-y-3"><legend class="mb-3 font-semibold text-text">{{ t('musicLora.simple.nameStep') }}</legend><label class="training-label">{{ t('musicLora.name') }}<input v-model="name" name="model-training-name" maxlength="120" required class="training-input" :disabled="busy" /></label></fieldset>
      <fieldset class="space-y-3 border-t border-border pt-4">
        <legend class="pr-3 font-semibold text-text">{{ t('musicLora.simple.tracksStep') }}</legend>
        <p class="text-sm text-text-dim">{{ t('musicLora.modelTraining.excerpt', { seconds }) }}</p>
        <p v-if="engine === 'heartmula'" class="text-sm text-text-dim">{{ t('musicLora.modelTraining.lyricsHint', { seconds }) }}</p>
        <div v-for="sample in samples" :key="trainingSampleKey(sample)" class="space-y-2 rounded-lg border border-border p-3">
          <div class="flex flex-wrap items-center justify-between gap-2"><h4 class="text-sm font-medium text-text">{{ sample.title }}</h4><button type="button" class="training-button" :disabled="busy" @click="samples = samples.filter(item => trainingSampleKey(item) !== trainingSampleKey(sample))">{{ t('musicLora.remove') }}</button></div>
          <label class="training-label">{{ t('musicLora.caption') }}<textarea v-model="sample.caption" :name="`model-caption-${trainingSampleKey(sample)}`" :placeholder="t('musicLora.simple.captionExample')" maxlength="4000" rows="2" required class="training-input" :disabled="busy" /></label>
          <label v-if="engine === 'heartmula'" class="flex min-h-11 items-center gap-2 text-sm text-text-dim"><input v-model="sample.instrumental" type="checkbox" :disabled="busy" @change="sample.instrumental && (sample.lyrics = '')" />{{ t('musicLora.modelTraining.instrumental') }}</label>
          <label v-if="engine === 'heartmula' && !sample.instrumental" class="training-label">{{ t('musicLora.modelTraining.lyrics') }}<textarea v-model="sample.lyrics" :name="`model-lyrics-${trainingSampleKey(sample)}`" maxlength="20000" rows="3" class="training-input" :disabled="busy" /></label>
        </div>
        <button type="button" class="training-button" :disabled="busy || samples.length >= 100" @click="pickerOpen = true">{{ t('musicLora.addSample') }}</button>
        <TrainingDatasetPicker :disabled="busy" :limit="100 - samples.length" @pick="addDataset" />
      </fieldset>
      <fieldset class="space-y-4 border-t border-border pt-4"><legend class="pr-3 font-semibold text-text"><span class="inline-flex items-center gap-1">{{ t('musicLora.simple.trainStep') }}<TrainingOptionsHelp :engine="engine" /></span></legend>
        <CollapsibleDetails :summary="t('musicLora.simple.advanced')"><div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label class="training-label">{{ t('musicLora.steps') }}<input v-model.number="maxSteps" name="model-training-steps" type="number" min="1" max="100000" step="1" class="training-input" required :disabled="busy" /></label>
          <label class="training-label">{{ t('musicLora.rank') }}<select v-model.number="rank" name="model-training-rank" class="training-input" :disabled="busy"><option v-for="value in [8, 16, 32]" :key="value" :value="value">{{ value }}</option></select></label>
          <label class="training-label">{{ t('musicLora.modelTraining.seconds') }}<input v-model.number="seconds" name="model-training-seconds" type="number" min="6" max="30" step="1" class="training-input" required :disabled="busy" /></label>
          <label class="training-label">{{ t('musicLora.learningRate') }}<input v-model.number="learningRate" name="model-training-learning-rate" type="number" min="0.0000001" max="0.01" step="any" class="training-input" required :disabled="busy" /></label>
          <label class="training-label">{{ t('musicLora.seed') }}<input v-model.number="seed" name="model-training-seed" type="number" min="0" max="2147483647" step="1" class="training-input" required :disabled="busy" /></label>
        </div></CollapsibleDetails>
        <p class="text-sm text-text-dim">{{ t('musicLora.simple.stepsSummary', { steps: maxSteps }) }}</p>
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
        <a v-if="job.kind === 'nvidia-package' && job.status === 'done' && job.package_sha256" :href="musicTrainingPackageUrl(job.id)" download class="inline-flex min-h-11 items-center text-sm text-accent1 underline">{{ t('musicLora.modelTraining.nvidia.download') }}</a>
        <template v-if="job.kind !== 'nvidia-package' && job.adapter">
          <a :href="musicAdapterFileUrl(job.adapter.id)" class="inline-flex min-h-11 items-center text-sm text-accent1 underline">{{ t('musicLora.download') }}</a>
          <div class="flex flex-wrap gap-4 text-sm"><RouterLink :to="`/music/${engine === 'yue2' ? 'yue2' : 'heartmula'}`" class="inline-flex min-h-11 items-center text-accent1 underline" @click="preview(job, true)">{{ t('musicLora.modelTraining.preview') }}</RouterLink><RouterLink :to="`/music/${engine === 'yue2' ? 'yue2' : 'heartmula'}`" class="inline-flex min-h-11 items-center text-accent1 underline" @click="preview(job, false)">{{ t('musicLora.modelTraining.compare') }}</RouterLink></div>
          <p class="text-xs text-text-dim">{{ t('musicLora.modelTraining.comparisonHint') }}<template v-if="engine === 'heartmula'"> {{ t('musicLora.modelTraining.heartBaseHint') }}</template></p>
        </template>
      </article>
    </section>
    <LibraryPicker v-if="pickerOpen" :track-only="true" :selected-track-ids="selectedTrackIds" @close="pickerOpen = false" @pick="pick" />
  </section>
</template>

<style scoped>
@reference "../../style.css";
.training-label { @apply flex flex-col gap-2 text-sm text-text-dim; }
.training-input { @apply w-full min-w-0 rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text outline-none focus:border-accent1; }
.training-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
