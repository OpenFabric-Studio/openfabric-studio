<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as api from '../../api/voices'
import { voiceErrorText } from '../../api/voices'
import type { VoiceProfile } from '../../api/voices'
import type { VoiceComparisonRequest, VoiceComparisonResponse, VoicePreparationResponse, VoiceSeparationOption, VoiceTrialSource } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import { useVoiceSession } from './useVoiceSession'
import VoiceTrialCard from './VoiceTrialCard.vue'
import { shortVoiceId } from './voiceLabels'

const props = defineProps<{ voice: VoiceProfile; preparation: VoicePreparationResponse | null; disabled?: boolean; active: boolean; selectionDirty?: boolean }>()
const { t } = useI18n()
const capture = useVoiceSession(() => props.voice.id)
const sources = ref<VoiceTrialSource[]>([])
const sourceId = ref('')
const jobs = ref<VoiceComparisonResponse[]>([])
const modelIds = ref<string[]>([])
const referenceIds = ref<string[]>([])
const steps = ref<(30 | 50)[]>([30])
const seed = ref(42)
const startSec = ref(0)
const durationSec = ref(10)
const inputKind = ref<NonNullable<VoiceComparisonRequest['input_kind']>>('song')
const quality = ref<NonNullable<VoiceComparisonRequest['separation_quality']>>('fast')
const separationOptions = ref<VoiceSeparationOption[]>([])
const action = ref('')
const error = ref('')
let sourceGeneration = 0
const modelChoices = computed(() => props.voice.models ?? [])
const referenceChoices = computed(() => (props.preparation?.references ?? []).filter((reference) => props.preparation?.selected_segment_ids?.includes(reference.segment_id)))
const active = (status: string | undefined) => status === 'queued' || status === 'running'
const trialCount = computed(() => modelIds.value.length * Math.max(1, referenceIds.value.length) * steps.value.length)
const source = computed(() => sources.value.find((item) => item.id === sourceId.value))
const canRun = computed(() => {
  if (props.disabled || action.value || api.isVoiceActive(props.voice.status) || active(props.preparation?.status) || jobs.value.some((job) => active(job.status))) return false
  if (props.selectionDirty && referenceIds.value.length > 0) return false
  const selected = source.value
  return !!selected && modelIds.value.length > 0 && modelIds.value.length <= 4 && referenceIds.value.length <= 3
    && steps.value.length > 0 && trialCount.value <= 12
    && Number.isFinite(startSec.value) && startSec.value >= 0
    && Number.isFinite(durationSec.value) && durationSec.value >= 2 && durationSec.value <= 30
    && startSec.value + durationSec.value <= selected.duration_sec
    && Number.isInteger(seed.value) && seed.value >= 0 && seed.value <= 4294967295
    && (inputKind.value === 'vocal' || separationOptions.value.some((option) => option.id === quality.value && option.available))
})
function errorText(cause: unknown) { return api.voiceErrorText(cause instanceof ApiError ? cause.message : 'unknown') }
function replaceJob(job: VoiceComparisonResponse) {
  poll.stop()
  const index = jobs.value.findIndex((item) => item.id === job.id)
  if (index < 0) jobs.value.unshift(job)
  else jobs.value[index] = job
  if (jobs.value.some((item) => active(item.status))) poll.start(false)
}
const poll = createPollingLoop(async (context) => {
  const session = capture()
  try {
    const response = await api.listVoiceComparisons(session.id, context.signal)
    if (!context.isCurrent() || !session.isCurrent()) return false
    jobs.value = response
  } catch (cause) {
    if (context.isCurrent() && session.isCurrent()) error.value = errorText(cause)
  }
  return jobs.value.some((job) => active(job.status))
}, 2000)
async function run(name: string, request: (session: ReturnType<typeof capture>) => Promise<void>) {
  if (action.value) return
  poll.stop()
  const session = capture()
  action.value = name
  error.value = ''
  try { await request(session) } catch (cause) { if (session.isCurrent()) error.value = errorText(cause) }
  finally { if (session.isCurrent()) { action.value = ''; if (jobs.value.some((job) => active(job.status))) poll.start(false) } }
}
async function upload(event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  const input = event.target
  const file = input.files?.[0]
  if (!file) return
  sourceGeneration++
  await run('upload', async (session) => {
    const result = await api.uploadVoiceTrialSource(session.id, file, session.signal)
    if (!session.isCurrent()) return
    sources.value = [result, ...sources.value.filter((item) => item.id !== result.id)]
    sourceId.value = result.id
    startSec.value = 0
    durationSec.value = Math.min(10, result.duration_sec)
    input.value = ''
  })
}
async function start() {
  if (!canRun.value) return
  const request: VoiceComparisonRequest = { source_id: sourceId.value, start_sec: startSec.value, duration_sec: durationSec.value, input_kind: inputKind.value, separation_quality: quality.value, model_ids: [...modelIds.value], reference_ids: [...referenceIds.value], diffusion_steps: [...steps.value], seed: seed.value }
  await run('start', async (session) => {
    const result = await api.startVoiceComparison(session.id, request, session.signal)
    if (session.isCurrent()) replaceJob(result)
  })
}
async function cancel(job: VoiceComparisonResponse) {
  await run('cancel', async (session) => {
    const result = await api.cancelVoiceComparison(session.id, job.id, session.signal)
    if (session.isCurrent()) replaceJob(result)
  })
}
async function loadSources() {
  const session = capture()
  const token = ++sourceGeneration
  try {
    const response = await api.listVoiceTrialSources(session.id, session.signal)
    if (!session.isCurrent() || token !== sourceGeneration) return
    sources.value = response
    if (!response.some((item) => item.id === sourceId.value)) sourceId.value = response[0]?.id ?? ''
    if (source.value) durationSec.value = Math.min(10, source.value.duration_sec)
  } catch (cause) { if (session.isCurrent() && token === sourceGeneration) error.value = errorText(cause) }
}
async function reload() {
  sourceGeneration++
  await run('reload', async (session) => {
    const [sourceRows, comparisons, options] = await Promise.all([api.listVoiceTrialSources(session.id, session.signal), api.listVoiceComparisons(session.id, session.signal), api.voiceSeparationOptions(session.signal)])
    if (!session.isCurrent()) return
    sources.value = sourceRows
    jobs.value = comparisons
    separationOptions.value = options
    if (!sources.value.some((item) => item.id === sourceId.value)) sourceId.value = sources.value[0]?.id ?? ''
  })
}
watch(modelChoices, (choices) => {
  modelIds.value = modelIds.value.filter((id) => choices.some((model) => model.id === id))
  if (!modelIds.value.length) {
    const activeModel = choices.find((model) => model.id === props.voice.active_model_id) ?? choices[0]
    if (activeModel) modelIds.value = [activeModel.id]
  }
}, { immediate: true })
watch(referenceChoices, (choices) => { referenceIds.value = referenceIds.value.filter((id) => choices.some((reference) => reference.id === id)) })
watch(() => props.voice.id, () => { poll.stop(); jobs.value = []; sources.value = []; sourceId.value = ''; action.value = ''; error.value = ''; referenceIds.value = []; void loadSources(); poll.start() })
onMounted(() => {
  void loadSources()
  poll.start()
  const session = capture()
  void api.voiceSeparationOptions(session.signal).then((options) => { if (session.isCurrent()) separationOptions.value = options }).catch((cause: unknown) => { if (session.isCurrent()) error.value = errorText(cause) })
})
onBeforeUnmount(() => { sourceGeneration++; poll.stop() })
</script>

<template>
  <section v-if="props.active" class="space-y-4">
    <h3 class="text-base font-semibold text-text">{{ t('voiceClone.compare.title') }}</h3>
    <p class="text-xs text-text-dim">{{ t('voiceClone.compare.intro') }}</p>
    <p v-if="selectionDirty && referenceIds.length" class="text-sm text-text-dim">{{ t('voiceClone.coverage.saveFirst') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.compare.upload') }}</span><input type="file" :disabled="!!action" accept="audio/*,.wav,.mp3,.flac,.ogg,.opus,.m4a" class="block w-full text-xs" @change="upload" /></label>
    <fieldset :disabled="!!action" class="space-y-3">
      <label class="block space-y-1 text-sm text-text"><span>{{ t('voiceClone.compare.source') }}</span><select v-model="sourceId" class="w-full rounded border border-border bg-panel-2 p-2"><option value="">{{ t('voiceClone.compare.chooseSource') }}</option><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.filename }} · {{ item.duration_sec.toFixed(1) }} {{ t('common.secondsUnit') }}</option></select></label>
      <div class="grid gap-3 sm:grid-cols-3"><label class="space-y-1 text-xs text-text"><span>{{ t('voiceClone.compare.start') }}</span><input v-model.number="startSec" type="number" min="0" step="0.1" class="w-full rounded border border-border bg-panel-2 p-2" /></label><label class="space-y-1 text-xs text-text"><span>{{ t('voiceClone.compare.duration') }}</span><input v-model.number="durationSec" type="number" min="2" max="30" step="0.1" class="w-full rounded border border-border bg-panel-2 p-2" /></label><label class="space-y-1 text-xs text-text"><span>{{ t('voiceClone.compare.seed') }}</span><input v-model.number="seed" type="number" min="0" max="4294967295" step="1" class="w-full rounded border border-border bg-panel-2 p-2" /></label></div>
      <div class="flex flex-wrap gap-3"><label class="space-y-1 text-xs text-text"><span>{{ t('voiceClone.compare.inputType') }}</span><select v-model="inputKind" class="block rounded border border-border bg-panel-2 p-2"><option value="song">{{ t('voiceClone.review.song') }}</option><option value="vocal">{{ t('voiceClone.review.vocal') }}</option></select></label><label class="space-y-1 text-xs text-text"><span>{{ t('voiceClone.review.separation') }}</span><select v-model="quality" class="block rounded border border-border bg-panel-2 p-2"><option v-for="option in separationOptions" :key="option.id" :value="option.id" :disabled="!option.available">{{ t(`voiceClone.review.quality.${option.id}`) }}{{ option.available ? '' : ` — ${t('voiceClone.review.unavailable')}` }}</option></select></label></div>
      <fieldset class="space-y-2"><legend class="text-sm font-medium text-text">{{ t('voiceClone.compare.models') }}</legend><label v-for="model in modelChoices" :key="model.id" class="mr-3 inline-flex items-center gap-2 text-xs text-text"><input v-model="modelIds" type="checkbox" :value="model.id" />{{ model.kind === 'base' ? `${t('voiceClone.review.referenceOnly')} · ${shortVoiceId(model.id)}` : `${t('voiceClone.review.trainedSteps', { steps: model.steps })} · ${shortVoiceId(model.id)}` }}</label></fieldset>
      <fieldset class="space-y-2"><legend class="text-sm font-medium text-text">{{ t('voiceClone.compare.references') }}</legend><p class="text-xs text-text-dim">{{ t('voiceClone.compare.referenceHint') }}</p><label v-for="reference in referenceChoices" :key="reference.id" class="block text-xs text-text"><input v-model="referenceIds" type="checkbox" :value="reference.id" class="mr-2" />{{ reference.source_filename }} · {{ reference.start_sec.toFixed(1) }}–{{ reference.end_sec.toFixed(1) }} {{ t('common.secondsUnit') }}</label></fieldset>
      <fieldset class="space-y-2"><legend class="text-sm font-medium text-text">{{ t('voiceClone.compare.steps') }}</legend><label v-for="count in [30, 50]" :key="count" class="mr-3 inline-flex items-center gap-2 text-xs text-text"><input v-model="steps" type="checkbox" :value="count" />{{ count }}</label></fieldset>
    </fieldset>
    <p class="text-xs text-text-dim">{{ t('voiceClone.compare.trialCount', { count: trialCount }) }}</p>
    <button type="button" :disabled="!canRun" class="rounded-lg bg-accent1 px-3 py-2 text-sm text-white disabled:opacity-50" @click="start">{{ t('voiceClone.compare.run') }}</button>
    <button type="button" :disabled="!!action" class="ml-3 text-xs text-text-dim hover:text-text disabled:opacity-50" @click="reload">{{ t('voiceClone.compare.reload') }}</button>
    <p class="text-xs text-text-dim">{{ t('voiceClone.compare.measurementHint') }}</p>
    <article v-for="job in jobs" :key="job.id" class="space-y-3 rounded-lg border border-border p-3">
      <div class="flex items-center justify-between gap-3"><p class="text-sm text-text">{{ job.source_filename }} · {{ t(`voiceClone.review.status.${job.status}`) }}</p><button v-if="active(job.status)" type="button" :disabled="!!action" class="text-xs text-text-dim" @click="cancel(job)">{{ t('common.cancel') }}</button></div>
      <p v-if="job.error_code" role="alert" class="text-xs text-status-failed">{{ voiceErrorText(job.error_code) }}</p>
      <VoiceTrialCard v-for="trial in job.trials" :key="trial.id" :voice-id="voice.id" :comparison-id="job.id" :trial="trial" :model-choices="modelChoices" :reference-candidates="preparation?.references ?? []" @rated="replaceJob" />
    </article>
  </section>
</template>
