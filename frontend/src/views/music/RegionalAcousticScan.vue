<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/regionalDatasets'
import { regionalAcousticAuditionUrl } from '../../api/regionalDatasets'
import { isDefiniteClientError } from '../../api/http'
import { parseRegionalAcousticReview, parseRegionalAcousticScanRequest } from '../../api/generated'
import type { RegionalAcousticCandidate, RegionalAcousticJob, RegionalAcousticReview, RegionalAcousticScanRequest, RegionalDatasetReview } from '../../api/generated'
import { createPollingLoop } from '../../composables/polling'

const props = defineProps<{ review: RegionalDatasetReview; disabled?: boolean }>()
const emit = defineEmits<{ apply: [value: RegionalAcousticReview | null]; busy: [value: boolean] }>()
const { t } = useI18n()
type CandidateDraft = { candidate: RegionalAcousticCandidate; distinct: boolean; reviewer: string; date: string; note: string; reviewed: boolean; auditionFailed: boolean }
const jobs = ref<RegionalAcousticJob[]>([]), selected = ref<RegionalAcousticJob | null>(null), rows = ref<CandidateDraft[]>([])
const busy = ref(false), loaded = ref(false), error = ref(false), disconnected = ref(false), pending = ref<RegionalAcousticScanRequest | null>(null), page = ref(0)
const revisionConflict = ref(false), draftBaseline = ref('[]'), reviewBasis = ref('')
const draftKey = computed(() => JSON.stringify(rows.value.map(row => [row.candidate.id, row.distinct, row.reviewer, row.date, row.note, row.reviewed])))
const draftChanged = computed(() => draftKey.value !== draftBaseline.value)
let alive = true, generation = 0, controller: AbortController | undefined
const active = computed(() => !!selected.value && (['queued', 'running'].includes(selected.value.status) || selected.value.error_code === 'regional_dataset_worker_unverified'))
const result = computed(() => selected.value?.result)
const visible = computed(() => rows.value.slice(page.value * 20, (page.value + 1) * 20))
const fits = computed(() => {
  const report = result.value?.report
  return !!report && report.excerpts.length === props.review.request.excerpts.length && report.excerpts.every(item => {
    const span = props.review.request.excerpts.find(value => value.id === item.excerpt_id)
    const recording = props.review.request.recordings.find(value => value.id === item.recording_id)
    return span && recording && span.recording_id === item.recording_id && span.start_frame === item.start_frame
      && span.end_frame === item.end_frame && recording.source_sha256 === item.source_sha256
  })
})
const validDecisions = computed(() => rows.value.every(row => !row.distinct || row.reviewed && row.reviewer.trim() && row.note.trim()
  && /^\d{4}-\d{2}-\d{2}$/.test(row.date) && !row.auditionFailed && !!row.candidate.left_audition && !!row.candidate.right_audition))
watch([busy, active], () => emit('busy', busy.value || active.value), { immediate: true })
function storageKey() { return `openfabric.regional-acoustic.v1.${props.review.request.dataset_id}` }
function remember(value: RegionalAcousticScanRequest | null) {
  pending.value = value
  try { if (value) sessionStorage.setItem(storageKey(), JSON.stringify(value)); else sessionStorage.removeItem(storageKey()) } catch { /* The backend still owns accepted scans. */ }
}
function readPending(): RegionalAcousticScanRequest | null {
  try {
    const raw = sessionStorage.getItem(storageKey())
    if (!raw || raw.length > 4096) return null
    const value: unknown = JSON.parse(raw), request = parseRegionalAcousticScanRequest(value)
    return request.dataset_id === props.review.request.dataset_id ? request : null
  } catch { return null }
}
function select(job: RegionalAcousticJob) {
  if (job.request.dataset_id !== props.review.request.dataset_id) throw new TypeError('Mismatched acoustic dataset')
  const changed = selected.value?.id !== job.id || selected.value.result?.sha256 !== job.result?.sha256
  selected.value = job
  jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id)]
  if (changed) loadRows(job)
}
function loadRows(job: RegionalAcousticJob) {
    const saved = props.review.request.acoustic_review
    rows.value = (job.result?.report.candidates ?? []).map(candidate => {
      const old = saved?.scan_id === job.id && saved.scan_sha256 === job.result?.sha256 ? saved.decisions?.find(item => item.candidate_id === candidate.id) : undefined
      return { candidate, distinct: old?.decision === 'distinct_after_review', reviewer: old?.reviewed_by ?? '',
        date: old?.reviewed_at ?? new Date().toISOString().slice(0, 10), note: old?.note ?? '', reviewed: !!old, auditionFailed: false }
    })
    page.value = 0
    draftBaseline.value = draftKey.value; reviewBasis.value = `${props.review.revision}:${props.review.sha256}`; revisionConflict.value = false
}
async function action(work: (signal: AbortSignal, current: () => boolean) => Promise<void>, readOnly = false) {
  if (!alive || busy.value || !readOnly && props.disabled) return
  poll.stop(); const token = ++generation, request = new AbortController(), current = () => alive && token === generation && !request.signal.aborted
  controller = request; busy.value = true; error.value = false
  try { await work(request.signal, current) }
  catch { if (current()) error.value = true }
  finally { if (current()) { busy.value = false; controller = undefined; if (active.value) poll.start(false) } }
}
const poll = createPollingLoop(async context => {
  const identifier = selected.value?.id
  if (!identifier || !active.value) return false
  try {
    const job = await api.getRegionalAcousticScan(identifier, context.signal)
    if (!alive || !context.isCurrent() || selected.value?.id !== identifier) return false
    select(job); disconnected.value = false
  } catch { if (alive && context.isCurrent()) disconnected.value = true }
  return active.value
}, 2500)
function load() {
  return action(async (signal, current) => {
    const response = await api.listRegionalAcousticScans(props.review.request.dataset_id, signal)
    if (!current()) return
    jobs.value = response.jobs; loaded.value = true
    const accepted = pending.value && response.jobs.find(item => item.request.submission_key === pending.value?.submission_key
      && item.request.review_revision === pending.value?.review_revision && item.request.review_sha256 === pending.value?.review_sha256)
    if (accepted) remember(null)
    const preserved = draftChanged.value ? selected.value : null
    const job = preserved && (response.jobs.find(item => item.id === preserved.id) ?? preserved) || accepted || response.jobs.find(item => ['queued', 'running'].includes(item.status) || item.error_code === 'regional_dataset_worker_unverified')
      || response.jobs.find(item => item.id === props.review.request.acoustic_review?.scan_id) || response.jobs[0]
    if (job) select(job)
  }, true)
}
function start() {
  if (pending.value || active.value || draftChanged.value || !loaded.value || props.disabled || busy.value) return
  remember(parseRegionalAcousticScanRequest({ dataset_id: props.review.request.dataset_id, review_revision: props.review.revision,
    review_sha256: props.review.sha256, submission_key: crypto.randomUUID().replaceAll('-', '') }))
  return resume()
}
function resume() {
  const request = pending.value
  if (!request || active.value) return
  return action(async (signal, current) => {
    try {
      const job = await api.startRegionalAcousticScan(request, signal)
      if (current()) { remember(null); select(job) }
    } catch (cause) {
      if (current() && isDefiniteClientError(cause)) remember(null)
      throw cause
    }
  })
}
function choose(event: Event) {
  if (!draftChanged.value && event.target instanceof HTMLSelectElement) {
    const identifier = event.target.value, job = jobs.value.find(item => item.id === identifier)
    if (job) select(job)
  }
}
function failedAudition(row: CandidateDraft) { row.auditionFailed = true; row.distinct = false; row.reviewed = false }
function cancel() {
  const identifier = selected.value?.id
  if (!identifier) return
  return action(async (signal, current) => { const job = await api.cancelRegionalAcousticScan(identifier, signal); if (current()) select(job) })
}
function apply() {
  if (!selected.value?.result || !fits.value || !validDecisions.value || revisionConflict.value || busy.value || active.value || pending.value || props.disabled) return
  emit('apply', parseRegionalAcousticReview(selection()))
}
function selection() {
  if (!selected.value?.result) return null
  const saved = props.review.request.acoustic_review
  const kept = saved?.scan_id === selected.value.id && saved.scan_sha256 === selected.value.result.sha256
    ? (saved.decisions ?? []).filter(item => item.decision === 'keep_together' && rows.value.some(row => row.candidate.id === item.candidate_id && !row.distinct)) : []
  return { scan_id: selected.value.id, scan_sha256: selected.value.result.sha256,
    decisions: [...kept, ...rows.value.filter(row => row.distinct).map(row => ({ candidate_id: row.candidate.id, decision: 'distinct_after_review',
      reviewed_by: row.reviewer.trim(), reviewed_at: row.date, note: row.note.trim() }))] }
}
function label(excerptId: string) {
  const excerpt = result.value?.report.excerpts.find(item => item.excerpt_id === excerptId)
  const recording = props.review.request.recordings.find(item => item.id === excerpt?.recording_id)
  return recording?.original_filename || recording?.source.sample_id || excerptId
}
watch(() => props.review.request.dataset_id, () => {
  generation++; controller?.abort(); controller = undefined; poll.stop(); busy.value = false; loaded.value = false
  selected.value = null; jobs.value = []; rows.value = []; draftBaseline.value = '[]'; reviewBasis.value = ''; revisionConflict.value = false; error.value = false; disconnected.value = false; pending.value = readPending(); void load()
}, { immediate: true })
watch(() => `${props.review.revision}:${props.review.sha256}`, value => {
  if (value === reviewBasis.value) return
  const proposed = selection(), saved = props.review.request.acoustic_review
  const ownSave = proposed && saved && proposed.scan_id === saved.scan_id && proposed.scan_sha256 === saved.scan_sha256
    && JSON.stringify(proposed.decisions) === JSON.stringify(saved.decisions ?? [])
  if (draftChanged.value && !ownSave) { revisionConflict.value = true; return }
  if (selected.value) loadRows(selected.value)
  else { reviewBasis.value = value; revisionConflict.value = false }
})
function reviewRevision() { if (!busy.value && !active.value && !props.disabled) { reviewBasis.value = `${props.review.revision}:${props.review.sha256}`; revisionConflict.value = false } }
function discard() { if (selected.value && !busy.value && !active.value && !props.disabled) loadRows(selected.value) }
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); poll.stop(); emit('busy', false) })
</script>
<template>
  <section class="space-y-3 rounded-lg border border-border p-4" :aria-label="t('regionalDatasets.acoustic.title')" :aria-busy="busy || active">
    <h4 class="font-semibold">{{ t('regionalDatasets.acoustic.title') }}</h4>
    <p class="text-sm text-text-dim">{{ t('regionalDatasets.acoustic.help') }}</p>
    <div class="flex flex-wrap gap-3"><button type="button" class="scan-button" :disabled="disabled || busy || active || draftChanged || !!pending || !loaded" @click="start">{{ t('regionalDatasets.acoustic.scan') }}</button><button type="button" class="scan-button" :disabled="busy" @click="load">{{ t('regionalDatasets.refresh') }}</button></div>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ t('regionalDatasets.acoustic.failed') }}</p>
    <p v-if="disconnected" role="status" class="text-sm text-status-queued">{{ t('regionalDatasets.acoustic.disconnected') }}</p>
    <div v-if="pending" role="status" class="space-y-2 text-sm"><p>{{ t('regionalDatasets.acoustic.pending') }}</p><button type="button" class="scan-button" :disabled="disabled || busy || active" @click="resume">{{ t('regionalDatasets.acoustic.resume') }}</button></div>
    <label v-if="jobs.length" class="scan-label">{{ t('regionalDatasets.acoustic.history') }}<select :value="selected?.id ?? ''" class="scan-input" :disabled="disabled || busy || active || draftChanged" @change="choose"><option v-for="job in jobs" :key="job.id" :value="job.id">{{ job.created_at }} · {{ t(`regionalDatasets.statuses.${job.status}`) }}</option></select></label>
    <div v-if="revisionConflict" role="status" class="space-y-2 rounded-lg border border-status-queued p-3 text-sm"><p>{{ t('regionalDatasets.acoustic.revisionConflict') }}</p><details><summary class="min-h-11 cursor-pointer py-2">{{ t('regionalDatasets.acoustic.savedDecisions') }}</summary><pre class="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{{ JSON.stringify(review.request.acoustic_review ?? null,null,2) }}</pre></details><button type="button" class="scan-button" :disabled="disabled || busy || active" @click="reviewRevision">{{ t('regionalDatasets.acoustic.reviewRevision') }}</button></div>
    <button v-if="draftChanged" type="button" class="scan-button" :disabled="disabled || busy || active" @click="discard">{{ t('regionalDatasets.acoustic.discard') }}</button>
    <p v-if="selected" role="status" class="text-sm">{{ t(`regionalDatasets.statuses.${selected.status}`) }}</p>
    <button v-if="active" type="button" class="scan-button" :disabled="disabled || busy" @click="cancel">{{ t('regionalDatasets.acoustic.cancel') }}</button>
    <p v-if="selected?.error_code" role="alert" class="text-sm text-status-failed">{{ t(`regionalDatasets.errors.${selected.error_code}`) }}</p>
    <template v-if="result && selected">
      <p class="text-sm text-text-dim">{{ t('regionalDatasets.acoustic.coverage', {screened:result.report.excerpts.filter(item => item.coverage === 'screened').length,total:result.report.excerpts.length,candidates:rows.length}) }}</p>
      <p class="text-sm text-status-queued">{{ t('regionalDatasets.acoustic.limits') }}</p>
      <p v-if="!fits" role="status" class="text-sm text-status-queued">{{ t('regionalDatasets.acoustic.stale') }}</p>
      <article v-for="row in visible" :key="row.candidate.id" class="space-y-3 rounded-lg border border-border p-3 text-sm">
        <h5 class="font-medium">{{ t('regionalDatasets.acoustic.pair') }} · {{ label(row.candidate.left_excerpt_id) }} / {{ label(row.candidate.right_excerpt_id) }}</h5>
        <p class="text-xs text-text-dim">{{ t('regionalDatasets.acoustic.basis', {seconds:row.candidate.aligned_seconds.toFixed(1),anchors:row.candidate.distinct_anchors}) }}</p>
        <div v-if="row.candidate.left_audition && row.candidate.right_audition" class="space-y-2"><p class="text-xs text-text-dim">{{ t('regionalDatasets.acoustic.audition') }}</p><audio controls preload="none" class="max-w-full" :src="regionalAcousticAuditionUrl(selected.id,row.candidate.id,'left')" :aria-label="label(row.candidate.left_excerpt_id)" @error="failedAudition(row)" /><audio controls preload="none" class="max-w-full" :src="regionalAcousticAuditionUrl(selected.id,row.candidate.id,'right')" :aria-label="label(row.candidate.right_excerpt_id)" @error="failedAudition(row)" /></div>
        <p v-else class="text-xs text-status-queued">{{ t('regionalDatasets.acoustic.noAudition') }}</p><p v-if="row.auditionFailed" role="alert" class="text-xs text-status-failed">{{ t('regionalDatasets.acoustic.auditionFailed') }}</p>
        <label class="flex min-h-11 items-center gap-3"><input v-model="row.distinct" type="checkbox" :disabled="disabled || busy || active || !fits || !row.candidate.left_audition || !row.candidate.right_audition || row.auditionFailed" />{{ t('regionalDatasets.acoustic.distinct') }}</label>
        <fieldset v-if="row.distinct" :disabled="disabled || busy || active" class="space-y-3"><label class="scan-label">{{ t('regionalDatasets.acoustic.reviewer') }}<input v-model="row.reviewer" required maxlength="120" class="scan-input" /></label><label class="scan-label">{{ t('regionalDatasets.acoustic.date') }}<input v-model="row.date" required type="date" class="scan-input" /></label><label class="scan-label">{{ t('regionalDatasets.acoustic.note') }}<textarea v-model="row.note" required maxlength="2000" class="scan-input" /></label><label class="flex min-h-11 items-center gap-3"><input v-model="row.reviewed" type="checkbox" required />{{ t('regionalDatasets.acoustic.confirm') }}</label></fieldset>
      </article>
      <div v-if="rows.length > 20" class="flex flex-wrap items-center gap-3"><button type="button" class="scan-button" :disabled="page === 0" @click="page--">{{ t('regionalDatasets.acoustic.previous') }}</button><span class="text-sm">{{ page + 1 }} / {{ Math.ceil(rows.length / 20) }}</span><button type="button" class="scan-button" :disabled="(page + 1) * 20 >= rows.length" @click="page++">{{ t('regionalDatasets.acoustic.next') }}</button></div>
      <button type="button" class="scan-button" :disabled="disabled || busy || active || !!pending || !fits || !validDecisions || revisionConflict" @click="apply">{{ t('regionalDatasets.acoustic.use') }}</button>
    </template>
    <button v-if="review.request.acoustic_review" type="button" class="scan-button" :disabled="disabled || busy || active || !!pending" @click="emit('apply',null)">{{ t('regionalDatasets.acoustic.clear') }}</button>
  </section>
</template>
<style scoped>
@reference '../../style.css';
.scan-label { @apply flex min-w-0 flex-col gap-1.5 text-xs text-text-dim; }
.scan-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2; }
.scan-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2 disabled:opacity-50; }
</style>
