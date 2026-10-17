<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/regionalDatasets'
import * as native from '../../api/stableLora'
import { cancelLocalMusic } from '../../api/localMusic'
import { ApiError } from '../../api/http'
import { parseRegionalDatasetReviewRequest, parseStableLoraTrainingRequest } from '../../api/generated'
import type { LoraDatasetOperationResponse, MusicLoraDatasetSource, RegionalComparisonState, RegionalDatasetPreview,
  RegionalBlindReveal, RegionalBlindSession, RegionalDatasetReview, RegionalExcerpt, RegionalListeningReview, RegionalModelCard, RegionalPreparationJob, RegionalRecordingInspection,
  RegionalAcousticReview, RegionalRecordingReview, RegionalTrainingSelection, StableLoraTrainingJob, StableLoraTrainingStatus } from '../../api/generated'
import { createPollingLoop } from '../../composables/polling'
import TrainingDatasetPicker from './TrainingDatasetPicker.vue'
import TrainingJobProgress from './TrainingJobProgress.vue'
import MusicTrainingSetup from './MusicTrainingSetup.vue'
import MusicTermsCard from '../../components/shared/MusicTermsCard.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import RegionalAcousticScan from './RegionalAcousticScan.vue'

type SpanDraft = { id: string; start: number; end: number }
type RowDraft = { id: string; source: MusicLoraDatasetSource; label: string; caption: string; record: RegionalRecordingReview | null;
  inspection: RegionalRecordingInspection | null; original: boolean; performer: string; session: string; nearCopy: string; spans: SpanDraft[] }
const props = defineProps<{ disabled?: boolean }>()
const { t, te } = useI18n()
const storageKey = 'openfabric-regional-preparation-v1'
const datasetId = ref(''), datasetRevision = ref(0), datasetName = ref(''), tradition = ref(''), splitSeed = ref(42)
const rows = ref<RowDraft[]>([]), saved = ref<RegionalDatasetReview | null>(null), reviewKnown = ref(false), historyKnown = ref(false)
const history = ref<RegionalPreparationJob[]>([]), prepared = ref<RegionalPreparationJob | null>(null)
const forecast = ref<RegionalDatasetPreview | null>(null), storageAccepted = ref(false), dirty = ref(false)
const selected = ref<RegionalTrainingSelection | null>(null), setup = ref<StableLoraTrainingStatus | null>(null)
const trainingJob = ref<StableLoraTrainingJob | null>(null), trainingHistoryKnown = ref(false)
const variant = ref<'small-music' | 'medium'>('small-music'), name = ref(''), steps = ref(1), rank = ref<8 | 16 | 32>(8), learningRate = ref(.0001), seed = ref(42)
const trainingTermsBlocked = ref(true), generationTermsBlocked = ref(true)
const comparison = ref<RegionalComparisonState | null>(null), comparisons = ref<RegionalComparisonState[]>([])
const comparisonPrompt = ref(''), comparisonSeconds = ref(5), comparisonSeed = ref(42)
const listener = ref(''), listenerScope = ref(''), preference = ref<RegionalListeningReview['preference']>('neither'), blindPreference = ref<'a' | 'b' | 'tie' | 'neither'>('tie'), notes = ref(''), publicAttribution = ref(''), consent = ref(false), heardBoth = ref(false)
const blindSession = ref<RegionalBlindSession | null>(null), blindReveal = ref<RegionalBlindReveal | null>(null), blindContaminated = ref(false), modelCard = ref<RegionalModelCard | null>(null)
const ratings = ref<Pick<RegionalListeningReview, 'timbre' | 'phrasing' | 'rhythm' | 'coherence' | 'artifacts' | 'cultural_appropriateness'>>({ timbre: 3, phrasing: 3, rhythm: 3, coherence: 3, artifacts: 3, cultural_appropriateness: 3 })
const operationWorking = ref(false), acousticBusy = ref(false), error = ref(''), reconnecting = ref(false), acousticNotice = ref('')
const working = computed(() => operationWorking.value || acousticBusy.value)
const permissionUses = ['reference', 'training', 'source_redistribution', 'adapter_distribution'] as const
const permissionValues = ['unknown', 'allowed', 'blocked'] as const
const restrictionValues = ['unknown', 'conditional', 'unrestricted_declared', 'noncommercial', 'no_derivatives'] as const
const ratingKeys = ['timbre', 'phrasing', 'rhythm', 'coherence', 'artifacts', 'cultural_appropriateness'] as const
let alive = true, generation = 0, controller: AbortController | undefined, preparationKey = '', comparisonKey = ''
function uuid(): string { return crypto.randomUUID().replaceAll('-', '') }
function ongoing(job: { status: string } | null | undefined): boolean { return !!job && ['queued', 'running'].includes(job.status) }
const weightsReady = computed(() => variant.value === 'medium' ? setup.value?.medium_weights_ready : setup.value?.small_weights_ready)
const canPrepare = computed(() => !props.disabled && !working.value && !dirty.value && historyKnown.value && forecast.value?.can_prepare && storageAccepted.value)
const canTrain = computed(() => !props.disabled && !working.value && !trainingTermsBlocked.value && selected.value && trainingHistoryKnown.value
  && setup.value?.supported && setup.value.installed && weightsReady.value && !ongoing(trainingJob.value) && name.value.trim().length > 0)
const bothOutputs = computed(() => comparison.value?.arms.every(arm => !!arm.output_sha256) === true)
function edited() { dirty.value = true; acousticNotice.value = ''; forecast.value = null; storageAccepted.value = false; preparationKey = ''; selected.value = null; trainingHistoryKnown.value = false }
function text(cause: unknown): string {
  const key = cause instanceof ApiError ? `regionalDatasets.errors.${cause.message}` : ''
  return t(te(key) ? key : 'regionalDatasets.failed')
}
function bytes(value: number | null | undefined): string { return value == null ? t('regionalDatasets.unknown') : `${(value / 1024 ** 2).toFixed(1)} MiB` }
async function action(work: (signal: AbortSignal, current: () => boolean) => Promise<void>) {
  if (!alive || props.disabled) return
  controller?.abort(); poll.stop()
  const token = ++generation, request = new AbortController(), current = () => alive && token === generation && !request.signal.aborted
  controller = request; operationWorking.value = true; error.value = ''
  try { await work(request.signal, current) }
  catch (cause) { if (current()) error.value = text(cause) }
  finally { if (current()) { operationWorking.value = false; controller = undefined; if (needsPoll()) poll.start(false) } }
}
function needsPoll(): boolean { return ongoing(prepared.value) || ongoing(trainingJob.value) || comparison.value?.arms.some(arm => ongoing(arm.job)) === true }
const poll = createPollingLoop(async context => {
  try {
    if (prepared.value && ongoing(prepared.value)) {
      const response = await api.getRegionalPreparation(prepared.value.id, context.signal)
      if (!alive || !context.isCurrent()) return false
      prepared.value = response
      if (response.status === 'done') await loadPrepared(response, context.signal, context.isCurrent)
    }
    if (trainingJob.value && ongoing(trainingJob.value)) {
      const response = await native.getStableLora(trainingJob.value.id, context.signal)
      if (!alive || !context.isCurrent()) return false
      trainingJob.value = response
    }
    if (comparison.value?.arms.some(arm => ongoing(arm.job))) {
      const response = await api.getRegionalComparison(comparison.value.plan.spec.id, context.signal)
      if (!alive || !context.isCurrent()) return false
      comparison.value = response
    }
    if (alive && context.isCurrent()) reconnecting.value = false
  } catch { if (alive && context.isCurrent()) reconnecting.value = true }
  return needsPoll()
}, 2500)

function hydrate(review: RegionalDatasetReview, preparedJob?: RegionalPreparationJob) {
  acousticNotice.value = ''
  saved.value = review; datasetId.value = review.request.dataset_id; datasetRevision.value = review.request.dataset_revision
  tradition.value = review.request.tradition; splitSeed.value = review.request.split_seed ?? 42
  const inspections = preparedJob?.result?.manifest.recordings ?? []
  rows.value = review.request.recordings.map(value => {
    const record = structuredClone(value), inspection = inspections.find(item => item.review.id === record.id && item.inspection.source_sha256 === record.source_sha256)?.inspection ?? null
    return { id: record.id, source: record.source, label: record.source.sample_id, caption: record.caption, record, inspection,
      original: record.original_recording_sha256 === record.source_sha256, performer: '', session: '', nearCopy: '',
      spans: review.request.excerpts.filter(item => item.recording_id === record.id).map(item => ({ id: item.id,
        start: inspection ? item.start_frame / inspection.sample_rate_hz : 0, end: inspection ? item.end_frame / inspection.sample_rate_hz : 0 })) }
  })
  dirty.value = false; forecast.value = null; storageAccepted.value = false; reviewKnown.value = true; preparationKey = ''; comparisonKey = ''
}
function choose(snapshot: LoraDatasetOperationResponse) {
  void action(async (signal, current) => {
    datasetId.value = snapshot.dataset.id; datasetRevision.value = snapshot.dataset.revision; datasetName.value = snapshot.dataset.name
    saved.value = null; prepared.value = null; selected.value = null; trainingJob.value = null; comparison.value = null; comparisons.value = []
    reviewKnown.value = false; historyKnown.value = false; forecast.value = null; storageAccepted.value = false; preparationKey = ''
    rows.value = snapshot.samples.filter(sample => sample.labeled).slice(0, 100).map(sample => ({ id: uuid(),
      source: { dataset_id: snapshot.dataset.id, revision: snapshot.dataset.revision, sample_id: sample.id }, label: sample.filename || sample.id,
      caption: sample.prompt_override === 'genre' ? sample.genre ?? '' : sample.caption ?? '', record: null, inspection: null,
      original: false, performer: '', session: '', nearCopy: '', spans: [] }))
    const results = await Promise.allSettled([api.loadRegionalReview(snapshot.dataset.id, signal), api.listRegionalPreparations(snapshot.dataset.id, signal)])
    if (!current()) return
    const [review, jobs] = results
    if (jobs.status === 'fulfilled') { history.value = jobs.value.jobs; historyKnown.value = true }
    if (review.status === 'fulfilled') hydrate(review.value)
    else if (review.reason instanceof ApiError && review.reason.status === 404) { reviewKnown.value = true; dirty.value = true }
    else throw review.reason
  })
}
function inspect(row: RowDraft) {
  void action(async (signal, current) => {
    const result = await api.inspectRegionalRecording({ recording_id: row.id, source: row.source }, signal)
    if (!current() || !rows.value.some(value => value.id === row.id)) return
    const changed = row.record && row.record.source_sha256 !== result.source_sha256
    if (!row.record || changed) {
      row.record = { id: row.id, source: row.source, source_sha256: result.source_sha256, caption: row.caption, rights: [], captions_reviewed: false }
      row.original = false; row.performer = ''; row.session = ''; row.nearCopy = ''
    }
    row.inspection = result
    if (!row.spans.length || changed || row.spans.some(span => span.end <= span.start)) {
      const retained = saved.value?.request.excerpts.filter(item => item.recording_id === row.id) ?? []
      row.spans = retained.length && !changed ? retained.map(item => ({ id: item.id, start: item.start_frame / result.sample_rate_hz,
        end: item.end_frame / result.sample_rate_hz })) : [{ id: uuid(), start: 0, end: Math.min(60, result.frames / result.sample_rate_hz) }]
    }
    edited()
  })
}
function addEvidence(row: RowDraft) {
  if (!row.record || (row.record.rights?.length ?? 0) >= 20) return
  row.record.rights ??= []
  row.record.rights.push({ id: uuid(), source_sha256: row.record.source_sha256, evidence_sha256: '', restriction: 'unknown',
    reviewed_by: '', reviewed_at: new Date().toISOString().slice(0, 10), permissions: {}, private_reference: '', public_url: null, public_attribution: '' })
  edited()
}
function originalChanged(row: RowDraft) {
  if (!row.original && row.record?.original_recording_sha256 === row.record?.source_sha256 && row.record) row.record.original_recording_sha256 = null
  edited()
}
async function groupHash(value: string, kind: string): Promise<string> {
  const input = new TextEncoder().encode(`${kind}:${value.normalize('NFKC').trim()}`)
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', input))].map(byte => byte.toString(16).padStart(2, '0')).join('')
}
function save() {
  if (!reviewKnown.value || !rows.value.length || rows.value.some(row => !row.record || !row.inspection)) { error.value = t('regionalDatasets.invalid'); return }
  void action(async (signal, current) => {
    const recordings: RegionalRecordingReview[] = [], excerpts: RegionalExcerpt[] = []
    for (const row of rows.value) {
      if (!row.record || !row.inspection) throw new TypeError('Missing inspection')
      recordings.push({ ...row.record, caption: row.caption,
        acquired_at: row.record.acquired_at || null,
        original_recording_sha256: row.original ? row.record.source_sha256 : row.record.original_recording_sha256 || null,
        performer_group_sha256: row.performer.trim() ? await groupHash(row.performer, 'performer') : row.record.performer_group_sha256 ?? null,
        session_group_sha256: row.session.trim() ? await groupHash(row.session, 'session') : row.record.session_group_sha256 ?? null,
        declared_near_copy_group_sha256: row.nearCopy.trim() ? await groupHash(row.nearCopy, 'near-copy') : row.record.declared_near_copy_group_sha256 ?? null,
        rights: row.record.rights?.map(evidence => ({ ...evidence, public_url: evidence.public_url || null, private_reference: evidence.private_reference || null })) ?? [] })
      for (const span of row.spans) excerpts.push({ id: span.id, recording_id: row.id,
        start_frame: Math.round(span.start * row.inspection.sample_rate_hz), end_frame: Math.round(span.end * row.inspection.sample_rate_hz) })
    }
    const previous = saved.value?.request
    const sources = (values: RegionalRecordingReview[]) => values.map(item => [item.id, item.source, item.source_sha256])
    const unchangedSelection = previous && JSON.stringify(sources(recordings)) === JSON.stringify(sources(previous.recordings)) && JSON.stringify(excerpts) === JSON.stringify(previous.excerpts)
    const request = parseRegionalDatasetReviewRequest({ dataset_id: datasetId.value, dataset_revision: datasetRevision.value,
      expected_revision: saved.value?.revision ?? 0, tradition: tradition.value, split_seed: splitSeed.value, recordings, excerpts,
      acoustic_review: unchangedSelection ? previous.acoustic_review ?? null : null })
    const response = await api.saveRegionalReview(request, signal)
    if (current()) { saved.value = response; dirty.value = false; forecast.value = null; storageAccepted.value = false; preparationKey = '' }
  })
}
function applyAcoustic(value: RegionalAcousticReview | null) {
  const review = saved.value
  if (!review || dirty.value || working.value) return
  void action(async (signal, current) => {
    const response = await api.saveRegionalReview({ ...review.request, expected_revision: review.revision, acoustic_review: value }, signal)
    if (current()) {
      saved.value = response; acousticNotice.value = value ? 'regionalDatasets.acoustic.saved' : 'regionalDatasets.acoustic.cleared'; forecast.value = null; storageAccepted.value = false
      preparationKey = ''; selected.value = null; trainingHistoryKnown.value = false
    }
  })
}
function preview() {
  const review = saved.value
  if (!review || dirty.value) return
  void action(async (signal, current) => {
    const response = await api.previewRegionalDataset({ dataset_id: review.request.dataset_id, review_revision: review.revision, review_sha256: review.sha256 }, signal)
    if (current() && saved.value?.sha256 === review.sha256 && !dirty.value) { forecast.value = response; storageAccepted.value = false }
  })
}
async function loadPrepared(job: RegionalPreparationJob, signal: AbortSignal, current: () => boolean) {
  if (!current()) return
  prepared.value = job
  try { localStorage.setItem(storageKey, job.id) } catch { /* Saved work remains in the backend. */ }
  if (!job.result || job.status !== 'done') return
  const selection = { manifest_id: job.id, manifest_sha256: job.result.sha256 }
  const results = await Promise.allSettled([api.regionalTrainingSelection(selection, signal), native.listStableLora(signal), api.listRegionalComparisons(job.id, signal)])
  if (!current() || prepared.value?.id !== job.id) return
  const [training, jobs, existing] = results
  selected.value = training.status === 'fulfilled' ? training.value : null
  if (training.status === 'rejected') error.value = text(training.reason)
  trainingHistoryKnown.value = jobs.status === 'fulfilled' && !jobs.value.history_incomplete
  if (jobs.status === 'fulfilled') trainingJob.value = jobs.value.jobs.find(item => item.request.samples.every(sample =>
    sample.regional_manifest?.manifest_id === job.id && sample.regional_manifest.manifest_sha256 === selection.manifest_sha256)) ?? null
  if (existing.status === 'fulfilled') { comparisons.value = existing.value.comparisons; comparison.value = comparisons.value[0] ?? null }
}
function prepare() {
  const review = saved.value
  if (!canPrepare.value || !review) return
  preparationKey ||= uuid()
  void action(async (signal, current) => {
    const job = await api.prepareRegionalDataset({ dataset_id: review.request.dataset_id, review_revision: review.revision,
      review_sha256: review.sha256, submission_key: preparationKey }, signal)
    await loadPrepared(job, signal, current)
  })
}
function freshPreparation() {
  if (!prepared.value || ongoing(prepared.value) || working.value || props.disabled) return
  preparationKey = uuid(); forecast.value = null; storageAccepted.value = false; selected.value = null
}
function reopen(identifier: string) {
  void action(async (signal, current) => {
    const job = await api.getRegionalPreparation(identifier, signal)
    const review = await api.loadRegionalReview(job.request.dataset_id, signal)
    if (!current()) return
    hydrate(review, job); historyKnown.value = true
    await loadPrepared(job, signal, current)
  })
}
function control(actionName: 'cancel' | 'retry-save') {
  if (!prepared.value) return
  const identifier = prepared.value.id
  void action(async (signal, current) => loadPrepared(await api.controlRegionalPreparation(identifier, actionName, signal), signal, current))
}
function train() {
  if (!canTrain.value || !selected.value) return
  const selection = selected.value
  void action(async (signal, current) => {
    const request = parseStableLoraTrainingRequest({ name: name.value.trim(), variant: variant.value, samples: selection.samples,
      max_steps: steps.value, rank: rank.value, learning_rate: learningRate.value, seed: seed.value })
    const response = await api.submitRegionalTraining({ manifest_id: selection.manifest_id, manifest_sha256: selection.manifest_sha256, request }, signal)
    if (current()) trainingJob.value = response
  })
}
function cancelTraining() {
  if (!trainingJob.value) return
  const identifier = trainingJob.value.id
  void action(async (signal, current) => { const response = await native.cancelStableLora(identifier, signal); if (current()) trainingJob.value = response })
}
function createComparison() {
  const job = trainingJob.value, selection = selected.value
  if (!selection || !job?.adapter || job.status !== 'done') return
  const adapter = job.adapter
  comparisonKey ||= uuid()
  void action(async (signal, current) => {
    const response = await api.createRegionalComparison({ manifest_id: selection.manifest_id, manifest_sha256: selection.manifest_sha256,
      submission_key: comparisonKey, adapter_id: adapter.id, adapter_sha256: adapter.sha256,
      request: { engine: 'stable_audio3', prompt: comparisonPrompt.value, duration_seconds: comparisonSeconds.value, seed: comparisonSeed.value,
        variant: job.request.variant ?? 'small-music', adapter: { id: adapter.id, strength: 1 } } }, signal)
    const state = await api.getRegionalComparison(response.spec.id, signal)
    if (current()) { comparison.value = state; comparisons.value = [state, ...comparisons.value.filter(item => item.plan.spec.id !== state.plan.spec.id)] }
  })
}
function armAction(arm: 'base' | 'adapter', actionName: 'submit' | 'collect') {
  const identifier = comparison.value?.plan.spec.id
  if (!identifier || actionName === 'submit' && generationTermsBlocked.value) return
  void action(async (signal, current) => { const state = await api.controlRegionalArm(identifier, arm, actionName, signal); if (current()) comparison.value = state })
}
function cancelArm(identifier: string) {
  const comparisonId = comparison.value?.plan.spec.id
  if (!comparisonId) return
  void action(async (signal, current) => { await cancelLocalMusic(identifier, signal); const state = await api.getRegionalComparison(comparisonId, signal); if (current()) comparison.value = state })
}
function openBlindReview() {
  const state = comparison.value
  if (!state || !heardBoth.value || !listener.value.trim()) return
  blindReveal.value = null
  void action(async (signal, current) => {
    const session = await api.openRegionalBlindSession(state.plan.spec.id, { reviewer_reference: listener.value }, signal)
    if (current()) { blindSession.value = session; blindContaminated.value = false }
  })
}
function loadModelCard() {
  const identifier = comparison.value?.plan.spec.id
  if (!identifier || blindSession.value && !blindReveal.value) return
  void action(async (signal, current) => {
    const card = await api.getRegionalModelCard(identifier, signal)
    if (current()) modelCard.value = card
  })
}
function showLabeledArms() {
  if (blindSession.value) blindContaminated.value = true
  blindSession.value = null
}
function saveBlindListening() {
  const state = comparison.value, session = blindSession.value
  if (!state || !session || blindContaminated.value || !heardBoth.value) return
  const [first, second] = session.clips
  if (!first || !second) return
  void action(async (signal, current) => {
    const reveal = await api.saveRegionalBlindReview(state.plan.spec.id, { comparison_sha256: session.comparison_sha256,
      assignment_sha256: session.assignment_sha256, label_a_sha256: first.output_sha256, label_b_sha256: second.output_sha256,
      reviewer_reference: session.reviewer_reference, reviewed_at: new Date().toISOString().slice(0, 10),
      tradition_scope: listenerScope.value, preference: blindPreference.value, ...ratings.value, notes: notes.value,
      public_attribution: publicAttribution.value, attribution_consented: consent.value }, signal)
    if (!current()) return
    blindReveal.value = reveal
    blindSession.value = null
    const refreshed = await api.getRegionalComparison(state.plan.spec.id, signal)
    if (current()) comparison.value = refreshed
  })
}
function saveListening() {
  const state = comparison.value, base = state?.arms.find(arm => arm.arm === 'base')?.output_sha256, adapter = state?.arms.find(arm => arm.arm === 'adapter')?.output_sha256
  if (!state || !base || !adapter || !heardBoth.value) return
  void action(async (signal, current) => {
    const response = await api.saveRegionalListeningReview(state.plan.spec.id, { comparison_sha256: state.plan.sha256,
      base_output_sha256: base, adapter_output_sha256: adapter, reviewer_reference: listener.value, reviewed_at: new Date().toISOString().slice(0, 10),
      tradition_scope: listenerScope.value, preference: preference.value, ...ratings.value, notes: notes.value,
      public_attribution: publicAttribution.value, attribution_consented: consent.value }, signal)
    if (current()) comparison.value = response
  })
}
watch([comparisonPrompt, comparisonSeconds, comparisonSeed, () => selected.value?.manifest_sha256, () => trainingJob.value?.adapter?.sha256], () => { comparisonKey = '' })
watch(() => comparison.value?.plan.spec.id, () => { heardBoth.value = false; blindSession.value = null; blindReveal.value = null; blindContaminated.value = false; modelCard.value = null })
watch(() => props.disabled, disabled => {
  if (disabled) { ++generation; controller?.abort(); controller = undefined; operationWorking.value = false; poll.stop() }
  else if (!setup.value) initialize()
  else if (needsPoll()) poll.start(true)
})
function initialize() {
  void action(async (signal, current) => {
    try { const status = await native.stableLoraStatus(signal); if (current()) setup.value = status }
    catch (cause) { if (current()) error.value = text(cause) }
    let identifier: string | null = null
    try { identifier = localStorage.getItem(storageKey) } catch { /* Reopen through the saved dataset if browser storage is unavailable. */ }
    if (!identifier || identifier.length !== 32 || !/^[a-f0-9]{32}$/.test(identifier)) return
    const job = await api.getRegionalPreparation(identifier, signal), review = await api.loadRegionalReview(job.request.dataset_id, signal)
    if (!current()) return
    hydrate(review, job); datasetName.value = review.request.tradition
    const previous = await api.listRegionalPreparations(job.request.dataset_id, signal)
    if (!current()) return
    history.value = previous.jobs; historyKnown.value = true
    await loadPrepared(job, signal, current)
  })
}
onMounted(initialize)
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort(); poll.stop() })
</script>

<template>
  <section class="space-y-4 rounded-xl border border-border bg-panel p-5" :aria-label="t('regionalDatasets.title')" :aria-busy="working">
    <h3 class="font-semibold text-text">{{ t('regionalDatasets.title') }}</h3>
    <p class="text-sm leading-relaxed text-text-dim">{{ t('regionalDatasets.intro') }}</p>
    <TrainingDatasetPicker :disabled="disabled" :limit="100" @pick="choose" />
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="reconnecting" role="status" class="text-sm text-status-queued">{{ t('regionalDatasets.reconnect') }}</p>
    <template v-if="datasetId">
      <h4 class="font-medium text-text">{{ datasetName }}</h4>
      <label class="regional-label">{{ t('regionalDatasets.tradition') }}<input v-model="tradition" name="regional-tradition" class="regional-input" maxlength="120" :disabled="working || disabled" @input="edited" /></label>
      <p v-if="dirty" role="status" class="text-sm text-status-queued">{{ t('regionalDatasets.dirty') }}</p>
      <div v-for="row in rows" :key="row.id" class="space-y-3 rounded-lg border border-border p-4">
        <div class="flex flex-wrap items-center justify-between gap-3"><h5 class="font-medium text-text">{{ row.label }}</h5><button type="button" class="regional-button" :disabled="working || disabled" @click="inspect(row)">{{ t('regionalDatasets.inspect') }}</button></div>
        <label class="regional-label">{{ t('regionalDatasets.caption') }}<textarea v-model="row.caption" :name="`regional-caption-${row.id}`" class="regional-input" maxlength="4000" :disabled="working || disabled" @input="edited" /></label>
        <template v-if="row.record && row.inspection">
          <p class="break-all text-xs text-text-dim">{{ t('regionalDatasets.source') }}: {{ row.record.source_sha256 }} · {{ row.inspection.sample_rate_hz }} Hz · {{ row.inspection.channels }} ch · {{ (row.inspection.frames / row.inspection.sample_rate_hz).toFixed(3) }} s</p>
          <div class="grid gap-3 sm:grid-cols-2"><label class="regional-label">{{ t('regionalDatasets.author') }}<input v-model="row.record.author" maxlength="200" class="regional-input" :disabled="working || disabled" @input="edited" /></label><label class="regional-label">{{ t('regionalDatasets.performerName') }}<input v-model="row.record.performer" maxlength="200" class="regional-input" :disabled="working || disabled" @input="edited" /></label><label class="regional-label">{{ t('regionalDatasets.acquiredAt') }}<input v-model="row.record.acquired_at" type="date" class="regional-input" :disabled="working || disabled" @input="edited" /></label><label class="regional-label">{{ t('regionalDatasets.originalFilename') }}<input v-model="row.record.original_filename" maxlength="255" class="regional-input" :disabled="working || disabled" @input="edited" /></label></div>
          <label class="regional-check"><input v-model="row.original" type="checkbox" :disabled="working || disabled" @change="originalChanged(row)" />{{ t('regionalDatasets.original') }}</label>
          <label v-if="!row.original" class="regional-label">{{ t('regionalDatasets.parent') }}<input v-model="row.record.original_recording_sha256" class="regional-input" minlength="64" maxlength="64" pattern="[0-9a-f]{64}" :disabled="working || disabled" @input="edited" /></label>
          <p class="text-xs text-text-dim">{{ t('regionalDatasets.groups') }}</p>
          <div class="grid gap-3 sm:grid-cols-2"><label class="regional-label">{{ t('regionalDatasets.performer') }}<input v-model="row.performer" class="regional-input" maxlength="200" :disabled="working || disabled" @input="edited" /><span v-if="row.record.performer_group_sha256" class="break-all text-xs">{{ t('regionalDatasets.pinned') }}: {{ row.record.performer_group_sha256 }}</span></label><label class="regional-label">{{ t('regionalDatasets.session') }}<input v-model="row.session" class="regional-input" maxlength="200" :disabled="working || disabled" @input="edited" /><span v-if="row.record.session_group_sha256" class="break-all text-xs">{{ t('regionalDatasets.pinned') }}: {{ row.record.session_group_sha256 }}</span></label></div>
          <label class="regional-label">{{ t('regionalDatasets.nearCopy') }}<input v-model="row.nearCopy" maxlength="200" class="regional-input" :disabled="working || disabled" @input="edited" /><span v-if="row.record.declared_near_copy_group_sha256" class="break-all text-xs">{{ t('regionalDatasets.pinned') }}: {{ row.record.declared_near_copy_group_sha256 }}</span></label>
          <div v-for="span in row.spans" :key="span.id" class="grid gap-3 sm:grid-cols-2"><label class="regional-label">{{ t('regionalDatasets.start') }}<input v-model.number="span.start" type="number" min="0" :max="row.inspection.frames / row.inspection.sample_rate_hz" step="any" class="regional-input" :disabled="working || disabled" @input="edited" /></label><label class="regional-label">{{ t('regionalDatasets.end') }}<input v-model.number="span.end" type="number" min="0.1" :max="row.inspection.frames / row.inspection.sample_rate_hz" step="any" class="regional-input" :disabled="working || disabled" @input="edited" /></label></div>
          <div class="grid gap-3 sm:grid-cols-3"><label class="regional-label">{{ t('regionalDatasets.instrument') }}<input v-model="row.record.instrument" class="regional-input" maxlength="120" :disabled="working || disabled" @input="edited" /></label><label class="regional-label">{{ t('regionalDatasets.technique') }}<input v-model="row.record.technique" class="regional-input" maxlength="120" :disabled="working || disabled" @input="edited" /></label><label class="regional-label">{{ t('regionalDatasets.tuning') }}<input v-model="row.record.tuning" class="regional-input" maxlength="200" :disabled="working || disabled" @input="edited" /></label></div>
          <label class="regional-check"><input v-model="row.record.captions_reviewed" type="checkbox" :disabled="working || disabled" @change="edited" />{{ t('regionalDatasets.captionsReviewed') }}</label>
          <label class="regional-check"><input v-model="row.record.evaluation_only" type="checkbox" :disabled="working || disabled" @change="edited" />{{ t('regionalDatasets.evaluationOnly') }}</label>
          <div v-for="evidence in row.record.rights" :key="evidence.id" class="space-y-3 rounded-lg border border-border bg-panel-2 p-3">
            <label class="regional-label">{{ t('regionalDatasets.evidenceHash') }}<input v-model="evidence.evidence_sha256" class="regional-input" minlength="64" maxlength="64" pattern="[0-9a-f]{64}" :disabled="working || disabled" @input="edited" /></label>
            <div class="grid gap-3 sm:grid-cols-2"><label class="regional-label">{{ t('regionalDatasets.reviewer') }}<input v-model="evidence.reviewed_by" class="regional-input" maxlength="120" :disabled="working || disabled" @input="edited" /></label><label class="regional-label">{{ t('regionalDatasets.date') }}<input v-model="evidence.reviewed_at" type="date" class="regional-input" :disabled="working || disabled" @input="edited" /></label></div>
            <label class="regional-label">{{ t('regionalDatasets.restriction') }}<select v-model="evidence.restriction" class="regional-input" :disabled="working || disabled" @change="edited"><option v-for="value in restrictionValues" :key="value" :value="value">{{ t(`regionalDatasets.restrictions.${value}`) }}</option></select></label>
            <div class="grid gap-3 sm:grid-cols-2"><label v-for="use in permissionUses" :key="use" class="regional-label">{{ t(`regionalDatasets.uses.${use}`) }}<select v-model="evidence.permissions[use]" class="regional-input" :disabled="working || disabled" @change="edited"><option v-for="value in permissionValues" :key="value" :value="value">{{ t(`regionalDatasets.permissions.${value}`) }}</option></select></label></div>
            <label class="regional-label">{{ t('regionalDatasets.publicUrl') }}<input v-model="evidence.public_url" type="url" class="regional-input" maxlength="2000" :disabled="working || disabled" @input="edited" /></label>
            <label class="regional-label">{{ t('regionalDatasets.privateRef') }}<input v-model="evidence.private_reference" class="regional-input" maxlength="120" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" :disabled="working || disabled" @input="edited" /></label>
            <label class="regional-label">{{ t('regionalDatasets.attribution') }}<textarea v-model="evidence.public_attribution" class="regional-input" maxlength="2000" :disabled="working || disabled" @input="edited" /></label>
            <button type="button" class="regional-button" :disabled="working || disabled" @click="row.record.rights = row.record.rights?.filter(item => item.id !== evidence.id); edited()">{{ t('regionalDatasets.removeEvidence') }}</button>
          </div>
          <button type="button" class="regional-button" :disabled="working || disabled || (row.record.rights?.length ?? 0) >= 20" @click="addEvidence(row)">{{ t('regionalDatasets.addEvidence') }}</button>
        </template>
      </div>
      <div class="flex flex-wrap gap-3"><button type="button" class="regional-button" :disabled="working || disabled || !reviewKnown || rows.some(row => !row.record || !row.inspection)" @click="save">{{ t('regionalDatasets.save') }}</button><button type="button" class="regional-button" :disabled="working || disabled || dirty || !saved" @click="preview">{{ t('regionalDatasets.preview') }}</button></div>
      <p v-if="dirty && saved?.request.acoustic_review" class="text-sm text-status-queued">{{ t('regionalDatasets.acoustic.edits') }}</p>
      <RegionalAcousticScan v-if="saved" :review="saved" :disabled="operationWorking || disabled || dirty" @busy="acousticBusy = $event" @apply="applyAcoustic" />
      <p v-if="acousticNotice" role="status" class="text-sm text-status-done">{{ t(acousticNotice) }}</p>
      <div v-if="forecast" class="space-y-3 rounded-lg border border-border p-4">
        <p class="text-sm text-text">{{ t('regionalDatasets.summary', { train: forecast.split.training_recordings, holdout: forecast.split.holdout_recordings, groups: forecast.split.independent_groups }) }}</p>
        <p v-for="warning in forecast.split.warnings" :key="warning" class="text-sm text-status-queued">{{ t(`regionalDatasets.warnings.${warning}`) }}</p>
        <ul class="space-y-1 text-sm text-text-dim"><li v-for="row in forecast.split.recordings" :key="row.recording_id">{{ rows.find(item => item.id === row.recording_id)?.label }} · {{ t(`regionalDatasets.${row.split === 'excluded' ? 'blocked' : row.split}`) }}<template v-if="row.exclusion_reason"> · {{ t(`regionalDatasets.reasons.${row.exclusion_reason}`) }}</template></li></ul>
        <p class="text-sm text-text-dim">{{ t('regionalDatasets.storage', { required: bytes(forecast.required_free_bytes), free: bytes(forecast.free_bytes) }) }}</p>
        <p v-if="forecast.error_code" role="alert" class="text-sm text-status-failed">{{ t(`regionalDatasets.errors.${forecast.error_code}`) }}</p>
        <label class="regional-check"><input v-model="storageAccepted" type="checkbox" :disabled="working || disabled || !forecast.can_prepare" />{{ t('regionalDatasets.acceptStorage') }}</label>
        <button type="button" class="regional-button" :disabled="!canPrepare" @click="prepare">{{ t('regionalDatasets.prepare') }}</button>
      </div>
      <CollapsibleDetails v-if="history.length" :summary="t('regionalDatasets.historical')"><div v-for="job in history" :key="job.id" class="flex flex-wrap items-center gap-3 text-sm text-text-dim"><span>{{ job.id }} · {{ t(`regionalDatasets.statuses.${job.status}`) }}</span><button type="button" class="regional-button" :disabled="working || disabled" @click="reopen(job.id)">{{ t('regionalDatasets.reopen') }}</button></div></CollapsibleDetails>
    </template>
    <div v-if="prepared" class="space-y-3 rounded-lg border border-border p-4">
      <p role="status" class="text-sm text-text">{{ t('regionalDatasets.preparation', { status: t(`regionalDatasets.statuses.${prepared.status}`), done: prepared.completed_items ?? 0, total: prepared.total_items ?? 0 }) }}</p>
      <p v-if="prepared.error_code" class="text-sm text-status-failed">{{ te(`regionalDatasets.errors.${prepared.error_code}`) ? t(`regionalDatasets.errors.${prepared.error_code}`) : t('regionalDatasets.failed') }}</p>
      <div class="flex flex-wrap gap-3"><button type="button" class="regional-button" :disabled="working || disabled" @click="reopen(prepared.id)">{{ t('regionalDatasets.refresh') }}</button><button v-if="ongoing(prepared)" type="button" class="regional-button" :disabled="working || disabled" @click="control('cancel')">{{ t('regionalDatasets.cancel') }}</button><button v-if="prepared.can_retry_save" type="button" class="regional-button" :disabled="working || disabled" @click="control('retry-save')">{{ t('regionalDatasets.retrySave') }}</button></div>
      <button v-if="!ongoing(prepared) && prepared.status !== 'done'" type="button" class="regional-button" :disabled="working || disabled" @click="freshPreparation">{{ t('regionalDatasets.freshPreparation') }}</button>
      <div v-if="prepared.result" class="space-y-2"><p class="break-all text-xs text-text-dim">SHA-256: {{ prepared.result.sha256 }}</p><div v-for="excerpt in prepared.result.manifest.excerpts" :key="excerpt.id" class="space-y-1"><p class="text-xs text-text-dim">{{ excerpt.recording_id }} · {{ t(`regionalDatasets.${excerpt.split}`) }} · {{ (excerpt.frames / excerpt.sample_rate_hz).toFixed(3) }} s</p><audio controls preload="none" class="max-w-full" :src="api.regionalExcerptUrl(prepared.id, excerpt.id)" :aria-label="`${t(`regionalDatasets.${excerpt.split}`)} ${excerpt.id}`" /></div></div>
    </div>
    <form v-if="selected" class="space-y-4 rounded-lg border border-border p-4" @submit.prevent="train">
      <h4 class="font-semibold text-text">{{ t('regionalDatasets.trainingTitle') }}</h4><p class="text-sm text-text-dim">{{ t('regionalDatasets.trainingHint') }}</p>
      <p class="text-sm text-text">{{ t('regionalDatasets.trainingCounts', { count: selected.samples.length, heldout: selected.held_out_excerpts }) }}</p>
      <p class="text-sm text-status-queued">{{ t('regionalDatasets.notCleared') }}</p>
      <MusicTermsCard engine="stable_audio3" operation="training" @blocked="trainingTermsBlocked = $event" />
      <MusicTrainingSetup v-if="setup" :variant="variant" :supported="setup.supported" :installed="setup.installed" :ready="!!weightsReady" @refresh="action(async (signal, current) => { const response = await native.stableLoraStatus(signal); if (current()) setup = response })" />
      <div class="grid gap-3 sm:grid-cols-2"><label class="regional-label">{{ t('regionalDatasets.name') }}<input v-model="name" name="regional-training-name" required maxlength="120" class="regional-input" :disabled="working || disabled" /></label><label class="regional-label">{{ t('regionalDatasets.variant') }}<select v-model="variant" class="regional-input" :disabled="working || disabled"><option value="small-music">Small Music</option><option value="medium">Medium</option></select></label><label class="regional-label">{{ t('regionalDatasets.steps') }}<input v-model.number="steps" type="number" min="1" max="100000" step="1" required class="regional-input" :disabled="working || disabled" /></label><label class="regional-label">{{ t('regionalDatasets.rank') }}<select v-model.number="rank" class="regional-input" :disabled="working || disabled"><option v-for="value in [8, 16, 32]" :key="value" :value="value">{{ value }}</option></select></label><label class="regional-label">{{ t('regionalDatasets.learningRate') }}<input v-model.number="learningRate" type="number" min="0.0000001" max="0.01" step="any" required class="regional-input" :disabled="working || disabled" /></label><label class="regional-label">{{ t('regionalDatasets.seed') }}<input v-model.number="seed" type="number" min="0" max="2147483647" step="1" required class="regional-input" :disabled="working || disabled" /></label></div>
      <button type="submit" class="regional-button" :disabled="!canTrain">{{ t('regionalDatasets.trainButton') }}</button>
      <TrainingJobProgress v-if="trainingJob" :job="trainingJob" :reconnecting="reconnecting" />
      <button v-if="ongoing(trainingJob)" type="button" class="regional-button" :disabled="working || disabled" @click="cancelTraining">{{ t('regionalDatasets.cancel') }}</button>
    </form>
    <form v-if="trainingJob?.status === 'done' && trainingJob.adapter && selected?.evaluation_sufficient" class="space-y-4 rounded-lg border border-border p-4" @submit.prevent="createComparison">
      <h4 class="font-semibold text-text">{{ t('regionalDatasets.comparisonTitle') }}</h4><p class="text-sm text-text-dim">{{ t('regionalDatasets.comparisonHint') }}</p>
      <label class="regional-label">{{ t('regionalDatasets.prompt') }}<textarea v-model="comparisonPrompt" required maxlength="4000" class="regional-input" :disabled="working || disabled" /></label>
      <div class="grid gap-3 sm:grid-cols-2"><label class="regional-label">{{ t('regionalDatasets.seconds') }}<input v-model.number="comparisonSeconds" type="number" min="5" :max="trainingJob.request.variant === 'medium' ? 380 : 120" step="1" required class="regional-input" :disabled="working || disabled" /></label><label class="regional-label">{{ t('regionalDatasets.seed') }}<input v-model.number="comparisonSeed" type="number" min="0" max="2147483647" step="1" required class="regional-input" :disabled="working || disabled" /></label></div>
      <button type="submit" class="regional-button" :disabled="working || disabled || !comparisonPrompt.trim()">{{ t('regionalDatasets.createComparison') }}</button>
    </form>
    <div v-if="comparison" class="space-y-4 rounded-lg border border-border p-4">
      <p class="break-all text-xs text-text-dim">{{ comparison.plan.spec.id }} · SHA-256: {{ comparison.plan.sha256 }}</p>
      <p class="text-sm text-status-queued">{{ t('regionalDatasets.unavailable') }}</p>
      <CollapsibleDetails :summary="t('regionalDatasets.runtimeEvidence')"><div class="space-y-2 text-xs text-text-dim"><p class="break-all">{{ t('regionalDatasets.manifestHash') }}: {{ comparison.plan.spec.request.manifest_sha256 }}</p><p class="break-all">{{ t('regionalDatasets.adapterHash') }}: {{ comparison.plan.spec.adapter.sha256 }}</p><p>{{ t(comparison.plan.spec.training_receipt.identity_status === 'worker_measured' ? 'regionalDatasets.measuredTraining' : 'regionalDatasets.unmeasuredTraining') }}</p><template v-if="comparison.plan.spec.training_receipt.runtime"><p class="break-all">{{ comparison.plan.spec.training_receipt.runtime.source_sha256 }} · Python {{ comparison.plan.spec.training_receipt.runtime.python_version }} · {{ comparison.plan.spec.training_receipt.runtime.platform }} / {{ comparison.plan.spec.training_receipt.runtime.machine }}</p><p v-for="model in comparison.plan.spec.training_receipt.runtime.models" :key="model.file_name" class="break-all">{{ model.file_name }} · {{ model.sha256 }} · {{ bytes(model.bytes) }}</p></template></div></CollapsibleDetails>
      <MusicTermsCard engine="stable_audio3" operation="generation" @blocked="generationTermsBlocked = $event" />
      <template v-if="!(blindSession && !blindReveal)">
      <div v-for="arm in comparison.arms" :key="arm.arm" class="space-y-3 border-t border-border pt-3">
        <p class="text-sm text-text">{{ arm.arm === 'base' ? t('musicLora.baseOnly') : t('regionalDatasets.preferenceOptions.adapter') }} · {{ arm.job ? t(`regionalDatasets.statuses.${arm.job.status}`) : t('regionalDatasets.armPending') }}</p>
        <p v-if="arm.submission_unverified" role="alert" class="text-sm text-status-queued">{{ t('regionalDatasets.submissionUnverified') }}</p>
        <button v-if="!arm.job" type="button" class="regional-button" :disabled="working || disabled || generationTermsBlocked || arm.submission_unverified" @click="armAction(arm.arm, 'submit')">{{ t(arm.arm === 'base' ? 'regionalDatasets.submitBase' : 'regionalDatasets.submitAdapter') }}</button>
        <button v-if="arm.job?.status === 'done' && !arm.output_sha256" type="button" class="regional-button" :disabled="working || disabled" @click="armAction(arm.arm, 'collect')">{{ t(arm.arm === 'base' ? 'regionalDatasets.collectBase' : 'regionalDatasets.collectAdapter') }}</button>
        <button v-if="arm.job && ongoing(arm.job)" type="button" class="regional-button" :disabled="working || disabled" @click="cancelArm(arm.job.id)">{{ t('regionalDatasets.cancel') }}</button>
        <audio v-if="arm.audio_url" controls preload="none" class="max-w-full" :src="arm.audio_url" :aria-label="t(`regionalDatasets.preferenceOptions.${arm.arm}`)" />
      </div>
      </template>
      <form v-if="bothOutputs && !blindSession" class="space-y-3 border-t border-border pt-4" @submit.prevent="saveListening">
        <h4 class="font-medium text-text">{{ t('regionalDatasets.listeningTitle') }}</h4><p class="text-xs text-text-dim">{{ t('regionalDatasets.ratingHint') }}</p>
        <label class="regional-label">{{ t('regionalDatasets.listener') }}<input v-model="listener" required maxlength="120" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" class="regional-input" :disabled="working || disabled" /></label>
        <label class="regional-label">{{ t('regionalDatasets.scope') }}<input v-model="listenerScope" required maxlength="200" class="regional-input" :disabled="working || disabled" /></label>
        <label class="regional-label">{{ t('regionalDatasets.preference') }}<select v-model="preference" class="regional-input" :disabled="working || disabled"><option v-for="value in ['base', 'adapter', 'tie', 'neither']" :key="value" :value="value">{{ t(`regionalDatasets.preferenceOptions.${value}`) }}</option></select></label>
        <div class="grid gap-3 sm:grid-cols-2"><label v-for="key in ratingKeys" :key="key" class="regional-label">{{ t(`regionalDatasets.ratings.${key}`) }}<input v-model.number="ratings[key]" type="number" min="1" max="5" step="1" required class="regional-input" :disabled="working || disabled" /></label></div>
        <label class="regional-label">{{ t('regionalDatasets.notes') }}<textarea v-model="notes" maxlength="4000" class="regional-input" :disabled="working || disabled" /></label>
        <label class="regional-label">{{ t('regionalDatasets.attribution') }}<input v-model="publicAttribution" maxlength="200" class="regional-input" :disabled="working || disabled" /></label>
        <label class="regional-check"><input v-model="consent" type="checkbox" :disabled="working || disabled" />{{ t('regionalDatasets.consent') }}</label>
        <label class="regional-check"><input v-model="heardBoth" type="checkbox" :disabled="working || disabled" />{{ t('regionalDatasets.heardBoth') }}</label>
        <button type="submit" class="regional-button" :disabled="working || disabled || !heardBoth || !listener.trim() || !listenerScope.trim() || !!publicAttribution && !consent">{{ t('regionalDatasets.saveListening') }}</button>
        <button type="button" class="regional-button" :disabled="working || disabled || !heardBoth || !listener.trim() || !listenerScope.trim()" @click="openBlindReview">{{ t('regionalDatasets.openBlind') }}</button>
      </form>
      <p v-if="blindContaminated" role="alert" class="text-sm text-status-queued">{{ t('regionalDatasets.blindContaminated') }}</p>
      <form v-if="blindSession && !blindReveal" class="space-y-3 border-t border-border pt-4" @submit.prevent="saveBlindListening">
        <h4 class="font-medium text-text">{{ t('regionalDatasets.blindTitle') }}</h4>
        <p class="text-xs text-text-dim">{{ t('regionalDatasets.blindHint') }}</p>
        <div v-for="clip in blindSession.clips" :key="clip.label" class="space-y-2">
          <p class="text-sm text-text">{{ t('regionalDatasets.blindClip', { label: clip.label.toUpperCase() }) }}</p>
          <audio controls preload="none" class="max-w-full" :src="clip.audio_url" :aria-label="t('regionalDatasets.blindClip', { label: clip.label.toUpperCase() })" />
        </div>
        <label class="regional-label">{{ t('regionalDatasets.preference') }}<select v-model="blindPreference" class="regional-input" :disabled="working || disabled"><option v-for="value in ['a', 'b', 'tie', 'neither']" :key="value" :value="value">{{ t(`regionalDatasets.blindPreferences.${value}`) }}</option></select></label>
        <div class="grid gap-3 sm:grid-cols-2"><label v-for="key in ratingKeys" :key="key" class="regional-label">{{ t(`regionalDatasets.ratings.${key}`) }}<input v-model.number="ratings[key]" type="number" min="1" max="5" step="1" required class="regional-input" :disabled="working || disabled" /></label></div>
        <label class="regional-label">{{ t('regionalDatasets.notes') }}<textarea v-model="notes" maxlength="4000" class="regional-input" :disabled="working || disabled" /></label>
        <label class="regional-label">{{ t('regionalDatasets.attribution') }}<input v-model="publicAttribution" maxlength="200" class="regional-input" :disabled="working || disabled" /></label>
        <label class="regional-check"><input v-model="consent" type="checkbox" :disabled="working || disabled" />{{ t('regionalDatasets.consent') }}</label>
        <div class="flex flex-wrap gap-3">
          <button type="submit" class="regional-button" :disabled="working || disabled || blindContaminated || !heardBoth || !listenerScope.trim() || !!publicAttribution && !consent">{{ t('regionalDatasets.saveBlind') }}</button>
          <button type="button" class="regional-button" :disabled="working || disabled" @click="showLabeledArms">{{ t('regionalDatasets.showLabels') }}</button>
        </div>
      </form>
      <p v-if="blindReveal" role="status" class="text-sm text-text">{{ t('regionalDatasets.blindReveal', { a: t(`regionalDatasets.preferenceOptions.${blindReveal.label_a_arm}`), b: t(`regionalDatasets.preferenceOptions.${blindReveal.label_b_arm}`), preference: t(`regionalDatasets.preferenceOptions.${blindReveal.decoded_preference}`) }) }}</p>
      <p v-if="bothOutputs" class="text-sm text-text-dim">{{ t('regionalDatasets.listenerCount', { count: comparison.listener_reviews, blind: comparison.blind_reviews ?? 0 }) }}</p>
      <div v-if="!(blindSession && !blindReveal)" class="space-y-3 border-t border-border pt-4">
        <button type="button" class="regional-button" :disabled="working || disabled" @click="loadModelCard">{{ t('regionalDatasets.modelCard') }}</button>
        <div v-if="modelCard" class="space-y-2 text-sm text-text-dim">
          <h4 class="font-medium text-text">{{ t('regionalDatasets.modelCardTitle') }}</h4>
          <p v-for="notice in modelCard.notices" :key="notice.code">{{ t(`regionalDatasets.modelCardNotices.${notice.code}`) }}</p>
          <p>{{ t('regionalDatasets.modelCardTraining', { tradition: modelCard.tradition, train: modelCard.training_recordings, holdout: modelCard.holdout_recordings, excluded: modelCard.excluded_recordings }) }}</p>
          <p>{{ t(modelCard.declared_adapter_distribution_permitted ? 'regionalDatasets.modelCardDistributionDeclared' : 'regionalDatasets.modelCardDistributionUndeclared') }}</p>
          <p>{{ t('regionalDatasets.modelCardReviews', { identified: modelCard.listener_reviews, unlabeled: modelCard.blind_reviews }) }}</p>
          <p v-if="modelCard.attributions.length === 0">{{ t('regionalDatasets.modelCardNoAttribution') }}</p>
          <ul v-else class="space-y-1"><li v-for="item in modelCard.attributions" :key="`${item.source}:${item.text}`">{{ item.text }}</li></ul>
          <p class="break-all">{{ modelCard.adapter_name }} · {{ modelCard.adapter_sha256 }}</p>
          <CollapsibleDetails v-if="modelCard.hashes.length" :summary="t('regionalDatasets.modelCardHashes')"><p v-for="item in modelCard.hashes" :key="`${item.role}:${item.sha256}`" class="break-all text-xs">{{ t(`regionalDatasets.hashRoles.${item.role}`) }} · {{ item.sha256 }}</p></CollapsibleDetails>
        </div>
      </div>
    </div>
    <CollapsibleDetails v-if="comparisons.length > 1" :summary="t('regionalDatasets.comparisonHistory')"><div class="space-y-2"><button v-for="state in comparisons" :key="state.plan.spec.id" type="button" class="regional-button block max-w-full break-words text-left" :disabled="working || disabled" @click="action(async (signal, current) => { const response = await api.getRegionalComparison(state.plan.spec.id, signal); if (current()) comparison = response })">{{ state.plan.spec.created_at }} · {{ state.plan.spec.request.request.prompt.slice(0, 100) }}</button></div></CollapsibleDetails>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.regional-label { @apply flex min-w-0 flex-col gap-2 text-sm text-text-dim; }
.regional-input { @apply min-h-11 min-w-0 rounded-lg border border-border bg-panel-2 px-3 py-2 text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
.regional-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
.regional-check { @apply flex min-h-11 items-center gap-2 text-sm text-text-dim; }
</style>
