<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import BeatPresetBrowser from './BeatPresetBrowser.vue'
import SpeechAudioPreview from '../voice/SpeechAudioPreview.vue'
import { pendingAceSongSource, pendingMusicConditioning } from '../../composables/generationDrafts'
import * as api from '../../api/beats'
import { beatArtifactUrl } from '../../api/beats'
import { ApiError } from '../../api/http'
import { parseBeatDocument } from '../../api/generated'
import type { BeatDocument, BeatDocumentSummary, BeatJob, BeatJobRequest, BeatKitInfo, BeatLane, BeatPattern, BeatRevisionInfo, BeatStep, BeatStoragePreview } from '../../api/generated'

const props = defineProps<{ beatId?: string; revision?: string }>()
const { t } = useI18n()
const documents = ref<BeatDocumentSummary[]>([]), kits = ref<BeatKitInfo[]>([]), jobs = ref<BeatJob[]>([]), revisions = ref<BeatRevisionInfo[]>([])
const saved = ref<BeatDocument | null>(null), draft = ref<BeatDocument | null>(null)
const pinned = ref<BeatDocument | null>(null), editorProjects = ref<Record<string, number>>({})
const referenceJob = ref<string | null>(null)
const patternId = ref<BeatPattern['id']>('A'), bar = ref(0), selectedStep = ref<{ lane: string, index: number } | null>(null)
const humanizeLanes = ref<string[]>([]), timingHumanize = ref(.05), velocityHumanize = ref(6)
const busy = ref(false), loading = ref(false), error = ref(''), notice = ref(''), approximate = ref(false)
const estimate = ref<BeatStoragePreview | null>(null), renderRequest = ref<BeatJobRequest | null>(null)
const manifestFile = ref<File | null>(null), archiveFile = ref<File | null>(null)
let alive = true, generation = 0, readGeneration = 0
let controller: AbortController | null = null, polling: AbortController | null = null, timer: ReturnType<typeof setTimeout> | null = null
const now = ref(Date.now())
let clock: ReturnType<typeof setInterval> | null = null
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value) !== JSON.stringify(saved.value))
const pattern = computed(() => draft.value?.patterns.find(item => item.id === patternId.value))
const kit = computed(() => kits.value.find(item => item.manifest.kit_id === draft.value?.kit_id && item.sha256 === draft.value?.kit_manifest_sha256))
const perBar = computed(() => pattern.value ? pattern.value.meter.numerator * 4 / pattern.value.meter.denominator * (pattern.value.steps_per_quarter ?? 4) : 16)
const indexes = computed(() => Array.from({ length: Math.min(256, Math.max(0, perBar.value)) }, (_, index) => bar.value * perBar.value + index))
const outsideHits = computed(() => {
  const count = perBar.value * (pattern.value?.bars ?? 1)
  return pattern.value?.lanes.reduce((total, lane) => total + (lane.steps ?? []).filter(item => item.index >= count).length, 0) ?? 0
})
const chosenLane = computed(() => pattern.value?.lanes.find(lane => lane.lane_id === selectedStep.value?.lane))
const chosenStep = computed(() => chosenLane.value?.steps?.find(step => step.index === selectedStep.value?.index))
const revisionMatches = computed(() => !!renderRequest.value && !dirty.value && renderRequest.value.expected_revision === saved.value?.revision && renderRequest.value.beat_id === saved.value.id)
function copy(value: BeatDocument): BeatDocument { return parseBeatDocument(JSON.parse(JSON.stringify(value))) }
function storageKey(id: string) { return `openfabric-beat-draft:${id}` }
function shortSize(value: number | null | undefined) { return value == null ? t('beats.unknown') : `${(value / 1024 ** 2).toLocaleString(undefined, { maximumFractionDigits: 1 })} MiB` }
function activeJob(job: BeatJob) { return ['queued', 'running', 'cancelling', 'saving'].includes(job.status) }
function fileUrl(job: BeatJob, kind: 'mix') { const artifact = job.manifest?.artifacts.find(item => item.kind === kind); return artifact ? api.beatArtifactUrl(job.id, artifact.id) : undefined }
function safeError(reason: unknown, fallback: string) { return reason instanceof ApiError && reason.message === 'beat_revision_conflict' ? t('beats.conflict') : t(fallback) }
function setDocument(value: BeatDocument, recover = false) {
  let raw: string | null = null
  try { if (recover) raw = localStorage.getItem(storageKey(value.id)) } catch { /* Saved revisions remain available without browser storage. */ }
  saved.value = copy(value); draft.value = copy(value); selectedStep.value = null; bar.value = 0
  patternId.value = value.patterns[0]?.id ?? 'A'; estimate.value = null; renderRequest.value = null
  if (recover) {
    try {
      if (raw && raw.length <= 2 * 1024 ** 2) {
        const recovered = parseBeatDocument(JSON.parse(raw))
        if (recovered.id === value.id && recovered.revision === value.revision && JSON.stringify(recovered) !== JSON.stringify(value)) {
          draft.value = recovered; notice.value = t('beats.restoredDraft')
        }
      }
    } catch { /* Invalid browser drafts never replace saved documents. */ }
  }
}
watch(draft, value => {
  estimate.value = null; renderRequest.value = null
  if (!value) return
  try { if (dirty.value) localStorage.setItem(storageKey(value.id), JSON.stringify(value)); else localStorage.removeItem(storageKey(value.id)) } catch { /* Backend-saved revisions remain authoritative. */ }
}, { deep: true, flush: 'sync' })
watch(patternId, () => { bar.value = 0; selectedStep.value = null; humanizeLanes.value = pattern.value?.lanes.map(lane => lane.lane_id) ?? [] })
watch(saved, () => { humanizeLanes.value = pattern.value?.lanes.map(lane => lane.lane_id) ?? [] })
watch(approximate, () => { estimate.value = null; renderRequest.value = null })
function begin() { controller?.abort(); controller = new AbortController(); busy.value = true; error.value = ''; notice.value = ''; return { token: ++generation, signal: controller.signal } }
function current(token: number) { return alive && token === generation }
function finish(token: number) { if (current(token)) { busy.value = false; loading.value = false; controller = null } }
async function load(id?: string, requestedRevision?: string) {
  if (busy.value || dirty.value) return
  const { token, signal } = begin(); loading.value = true
  stopPolling()
  try {
    const [listed, available] = await Promise.all([api.listBeats(signal), api.listBeatKits(signal)])
    if (!current(token)) return
    documents.value = listed.documents; kits.value = available.kits
    const selected = id ?? listed.documents[0]?.id
    if (selected) {
      const [value, history, versions] = await Promise.all([api.getBeat(selected, signal), api.listBeatJobs(selected, signal), api.listBeatRevisions(selected, signal)])
      if (!current(token)) return
      let requested: BeatDocument | null = null
      if (requestedRevision !== undefined) {
        if (!/^\d{1,16}$/.test(requestedRevision) || !Number.isSafeInteger(Number(requestedRevision))) throw new TypeError('Invalid beat revision')
        requested = await api.getBeatRevision(selected, Number(requestedRevision), signal)
        if (!current(token)) return
      }
      setDocument(value, true); pinned.value = requested; jobs.value = history.jobs; revisions.value = versions.revisions
    } else { saved.value = null; draft.value = null; jobs.value = []; revisions.value = [] }
  } catch (reason: unknown) { if (current(token)) error.value = safeError(reason, 'beats.loadFailed') }
  finally { finish(token); if (current(token)) schedulePolling() }
}
async function newBeat() {
  if (busy.value || dirty.value) return
  const { token, signal } = begin()
  try {
    const value = await api.createBeat({ title: t('beats.new'), kit_id: 'procedural-core', bpm: 120, seed: 4471 }, signal)
    if (!current(token)) return
    setDocument(value); const listed = await api.listBeats(signal)
    if (!current(token)) return
    documents.value = listed.documents; const history = await api.listBeatRevisions(value.id, signal)
    if (!current(token)) return
    jobs.value = []; revisions.value = history.revisions
  } catch (reason: unknown) { if (current(token)) error.value = safeError(reason, 'beats.saveFailed') }
  finally { finish(token) }
}
async function createPreset(document: BeatDocument) {
  if (busy.value || dirty.value) return
  const { token, signal } = begin()
  try {
    const value = await api.createBeat({ document }, signal)
    if (!current(token)) return
    setDocument(value); jobs.value = []
    const [listed, history] = await Promise.all([api.listBeats(signal), api.listBeatRevisions(value.id, signal)])
    if (!current(token)) return
    documents.value = listed.documents; revisions.value = history.revisions
  } catch (reason: unknown) { if (current(token)) error.value = safeError(reason, 'beats.saveFailed') }
  finally { finish(token) }
}
async function save() {
  if (busy.value || !draft.value || !saved.value || !dirty.value || outsideHits.value) return
  const original = saved.value, editing = draft.value
  const { token, signal } = begin()
  try {
    const submitted = copy(editing)
    const value = await api.saveBeat(original.id, { expected_revision: original.revision, document: submitted }, signal)
    if (!current(token)) return
    setDocument(value); notice.value = t('beats.saved', { revision: value.revision })
    const [listed, history] = await Promise.all([api.listBeats(signal), api.listBeatRevisions(value.id, signal)])
    if (!current(token)) return
    documents.value = listed.documents; revisions.value = history.revisions
  } catch (reason: unknown) { if (current(token)) error.value = safeError(reason, 'beats.saveFailed') }
  finally { finish(token) }
}
function discard() { if (saved.value && !busy.value) { setDocument(saved.value); notice.value = '' } }
function usePinned() {
  if (busy.value || dirty.value || !saved.value || !pinned.value || saved.value.id !== pinned.value.id) return
  draft.value = { ...copy(pinned.value), revision: saved.value.revision }
  patternId.value = draft.value.patterns[0]?.id ?? 'A'
}
async function prepareEditor(job: BeatJob) {
  if (busy.value || job.status !== 'done' || job.request.mode !== 'full') return
  const { token, signal } = begin()
  try {
    const result = await api.publishBeatProject(job.id, job.beat_id, job.beat_revision, signal)
    if (current(token)) editorProjects.value = { ...editorProjects.value, [job.id]: result.project_id }
  } catch { if (current(token)) error.value = t('beats.projectFailed') }
  finally { finish(token) }
}
function useReference(job: BeatJob) {
  const artifact = job.manifest?.artifacts.find(item => item.kind === 'mix')
  if (busy.value || job.status !== 'done' || job.request.mode !== 'full' || !artifact) return
  pendingMusicConditioning.value = { source: { kind: 'beat_render', beat_id: job.beat_id, beat_revision: job.beat_revision, render_id: job.id, sha256: artifact.sha256 }, label: `${saved.value?.title ?? t('beats.title')} · ${t('beats.revision', { revision: job.beat_revision })}` }
  referenceJob.value = job.id
}
function createSong(job: BeatJob) {
  const artifact = job.manifest?.artifacts.find(item => item.kind === 'mix')
  if (busy.value || job.status !== 'done' || job.request.mode !== 'full' || !artifact) return
  pendingAceSongSource.value = { source: { kind: 'beat_render', beat_id: job.beat_id, beat_revision: job.beat_revision, render_id: job.id, sha256: artifact.sha256 }, label: `${saved.value?.title ?? t('beats.title')} · ${t('beats.revision', { revision: job.beat_revision })}` }
}
async function mutate(action: 'variation' | 'humanize' | 'restore', revision?: number) {
  if (busy.value || dirty.value || !saved.value || !pattern.value) return
  if (action === 'humanize' && (!humanizeLanes.value.length || !Number.isFinite(timingHumanize.value) || timingHumanize.value < 0 || timingHumanize.value > .1 || !Number.isInteger(velocityHumanize.value) || velocityHumanize.value < 0 || velocityHumanize.value > 12)) return
  const value = saved.value, selected = pattern.value
  const { token, signal } = begin()
  try {
    const result = action === 'variation' ? await api.varyBeat(value.id, { expected_revision: value.revision, pattern_id: selected.id, seed: value.seed }, signal)
      : action === 'humanize' ? await api.humanizeBeat(value.id, { expected_revision: value.revision, pattern_id: selected.id, lane_ids: humanizeLanes.value, timing_steps: timingHumanize.value, velocity: velocityHumanize.value }, signal)
      : await api.restoreBeat(value.id, { expected_revision: value.revision, revision: revision ?? value.revision }, signal)
    if (!current(token)) return
    setDocument(result); patternId.value = selected.id
    const history = await api.listBeatRevisions(value.id, signal)
    if (!current(token)) return
    revisions.value = history.revisions
  } catch (reason: unknown) { if (current(token)) error.value = safeError(reason, 'beats.saveFailed') }
  finally { finish(token) }
}
function toggleHumanize(lane: string, event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return
  humanizeLanes.value = event.target.checked ? [...new Set([...humanizeLanes.value, lane])] : humanizeLanes.value.filter(item => item !== lane)
}
async function archive() {
  if (busy.value || dirty.value || !saved.value) return
  const value = saved.value, { token, signal } = begin()
  let archived = false
  try {
    await api.archiveBeat(value.id, value.revision, signal)
    if (current(token)) { saved.value = null; draft.value = null; pinned.value = null; archived = true }
  } catch (reason: unknown) { if (current(token)) error.value = safeError(reason, 'beats.saveFailed') }
  finally { finish(token) }
  if (archived && current(token)) void load()
}
function step(lane: BeatLane, index: number): BeatStep | undefined { return lane.steps?.find(value => value.index === index) }
function toggleStep(lane: BeatLane, index: number, inspect = false) {
  if (busy.value) return
  selectedStep.value = { lane: lane.lane_id, index }
  lane.steps ??= []
  const existing = lane.steps.findIndex(value => value.index === index)
  if (inspect && existing >= 0) return
  if (existing >= 0) lane.steps.splice(existing, 1)
  else lane.steps.push({ index, velocity: 100, probability: 1, offset_steps: 0, ratchet: 1, gate_steps: 1, note_midi: lane.role === 'drum' ? null : 60, cents: 0 })
}
function inspectStep(lane: BeatLane, index: number) { selectedStep.value = { lane: lane.lane_id, index } }
function addPitchPoint() {
  const selected = chosenStep.value
  if (!selected || busy.value) return
  selected.pitch_curve ??= []
  if (!selected.pitch_curve.length) { selected.pitch_curve = [{ position: 0, cents: 0 }, { position: 1, cents: 0 }]; return }
  if (selected.pitch_curve.length >= 16) return
  let gap = 0, at = 0
  for (let index = 1; index < selected.pitch_curve.length; index++) {
    const left = selected.pitch_curve[index - 1], right = selected.pitch_curve[index]
    if (left && right && right.position - left.position > gap) { gap = right.position - left.position; at = index }
  }
  const left = selected.pitch_curve[at - 1], right = selected.pitch_curve[at]
  if (left && right && gap > .000001) selected.pitch_curve.splice(at, 0, { position: (left.position + right.position) / 2, cents: (left.cents + right.cents) / 2 })
}
function articulations() {
  return [...new Set(kit.value?.manifest.instruments.find(item => item.instrument_id === chosenLane.value?.instrument_id)?.regions.flatMap(item => item.articulation_id ? [item.articulation_id] : []) ?? [])]
}
function removeStep() { const lane = chosenLane.value, selected = selectedStep.value; if (lane && selected) lane.steps = lane.steps?.filter(item => item.index !== selected.index) }
function addPattern() {
  if (!draft.value || !pattern.value || draft.value.patterns.length >= 4 || outsideHits.value) return
  const next = (['A', 'B', 'C', 'D'] as const).find(id => !draft.value?.patterns.some(item => item.id === id))
  if (!next) return
  draft.value.patterns.push({ ...pattern.value, id: next, lanes: pattern.value.lanes.map(lane => ({ ...lane, steps: (lane.steps ?? []).map(value => ({ ...value, pitch_curve: value.pitch_curve?.map(point => ({ ...point })) })) })) })
  patternId.value = next
}
function resizeGrid() {
  if (!pattern.value) return
  bar.value = Math.min(bar.value, (pattern.value.bars ?? 1) - 1)
}
function removeOutsideHits() {
  if (!pattern.value || busy.value) return
  const count = perBar.value * (pattern.value.bars ?? 1)
  if (!Number.isSafeInteger(count) || count < 1) return
  for (const lane of pattern.value.lanes) lane.steps = lane.steps?.filter(item => item.index < count)
}
function selectKit(event: Event) {
  if (!(event.target instanceof HTMLSelectElement) || !draft.value) return
  const selected = event.target.value
  const found = kits.value.find(value => value.manifest.kit_id === selected)
  if (found) { draft.value.kit_id = found.manifest.kit_id; draft.value.kit_manifest_sha256 = found.sha256 }
}
function selectBeat(event: Event) { if (event.target instanceof HTMLSelectElement) void load(event.target.value) }
async function reviewRender(mode: BeatJobRequest['mode']) {
  if (busy.value || dirty.value || !saved.value || !pattern.value || !kit.value) return
  const request: BeatJobRequest = { beat_id: saved.value.id, expected_revision: saved.value.revision, mode, preview_pattern_id: mode === 'preview' ? pattern.value.id : null, submission_key: crypto.randomUUID().replaceAll('-', ''), midi_approximation: approximate.value }
  const { token, signal } = begin()
  try {
    const result = await api.previewBeatStorage(request.beat_id, request, signal)
    if (!current(token)) return
    renderRequest.value = request; estimate.value = result
  } catch (reason: unknown) { if (current(token)) error.value = safeError(reason, 'beats.diskUnavailable') }
  finally { finish(token) }
}
async function startRender() {
  if (busy.value || !estimate.value?.can_start || !renderRequest.value || !revisionMatches.value) return
  const request = renderRequest.value
  const { token, signal } = begin()
  try {
    const result = await api.renderBeat(request.beat_id, request, signal)
    if (!current(token)) return
    jobs.value = [result, ...jobs.value.filter(item => item.id !== result.id)]
    estimate.value = null; renderRequest.value = null; schedulePolling()
  } catch (reason: unknown) { if (current(token)) { estimate.value = null; error.value = safeError(reason, 'beats.renderFailed'); void refreshJobs() } }
  finally { finish(token) }
}
function stopPolling() { readGeneration++; polling?.abort(); polling = null; if (timer) clearTimeout(timer); timer = null }
function schedulePolling() { if (alive && jobs.value.some(activeJob) && !timer) timer = setTimeout(() => { timer = null; void refreshJobs() }, 1500) }
async function refreshJobs() {
  const beatId = saved.value?.id
  if (!beatId) return
  const token = ++readGeneration
  polling?.abort(); const request = new AbortController(); polling = request
  try {
    const result = await api.listBeatJobs(beatId, request.signal)
    if (!alive || token !== readGeneration || saved.value?.id !== beatId) return
    jobs.value = result.jobs
  } catch { if (alive && token === readGeneration) error.value = t('beats.loadFailed') }
  finally { if (alive && token === readGeneration) { polling = null; schedulePolling() } }
}
async function jobAction(job: BeatJob, action: 'cancel' | 'save') {
  if (busy.value) return
  const { token, signal } = begin()
  try {
    const result = await (action === 'cancel' ? api.cancelBeatJob : api.retryBeatSave)(job.id, signal)
    if (!current(token)) return
    jobs.value = jobs.value.map(item => item.id === job.id ? result : item); schedulePolling()
  } catch { if (current(token)) error.value = t('beats.renderFailed') }
  finally { finish(token) }
}
function elapsed(job: BeatJob): number { return Math.max(0, Math.round(((job.finished_at ? Date.parse(job.finished_at) : now.value) - Date.parse(job.started_at ?? job.created_at)) / 1000)) }
function eta(job: BeatJob): number | null { const seconds = elapsed(job); return job.status === 'running' && job.progress_frames && job.total_frames && job.progress_frames < job.total_frames && seconds > 0 ? Math.round(seconds / job.progress_frames * (job.total_frames - job.progress_frames)) : null }
function pickFile(event: Event, target: 'manifest' | 'archive') { if (event.target instanceof HTMLInputElement) { const file = event.target.files?.[0] ?? null; if (target === 'manifest') manifestFile.value = file; else archiveFile.value = file } }
async function importKit() {
  if (busy.value || !manifestFile.value || !archiveFile.value) return
  const { token, signal } = begin()
  try { const result = await api.importBeatKit(manifestFile.value, archiveFile.value, signal); if (current(token)) kits.value = result.kits }
  catch { if (current(token)) error.value = t('beats.kitFailed') }
  finally { finish(token) }
}
watch(() => [props.beatId, props.revision], () => { if (dirty.value) notice.value = t('beats.unsaved'); else void load(props.beatId, props.revision) })
onMounted(() => { clock = setInterval(() => { now.value = Date.now() }, 1000); void load(props.beatId, props.revision) })
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); stopPolling(); if (clock) clearInterval(clock) })
</script>

<template>
  <div class="min-w-0 space-y-5" :aria-busy="busy">
    <div class="flex flex-wrap items-start justify-between gap-3"><div><h2 class="text-xl font-semibold">{{ t('beats.title') }}</h2><p class="mt-2 max-w-3xl text-sm text-text-dim">{{ t('beats.intro') }}</p></div><button type="button" class="beat-button" :disabled="busy || dirty" @click="newBeat">{{ t('beats.new') }}</button></div>
    <p v-if="loading" role="status">{{ t('beats.loading') }}</p><p v-else-if="busy" role="status" class="text-sm text-text-dim">{{ t('beats.busy') }}</p>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p><p v-if="notice" role="status" class="text-sm text-accent2">{{ notice }}</p>
    <BeatPresetBrowser :disabled="busy || dirty" @candidate="createPreset" />
    <label class="beat-label"><span>{{ t('beats.library') }}</span><select class="beat-input" :value="saved?.id ?? ''" :disabled="busy || dirty" @change="selectBeat"><option v-if="!documents.length" value="">{{ t('beats.new') }}</option><option v-for="item in documents" :key="item.id" :value="item.id">{{ item.title }} · {{ t('beats.revision', { revision: item.revision }) }}</option></select></label>
    <button v-if="!draft && !busy" type="button" class="beat-button" @click="load()">{{ t('beats.refresh') }}</button>
    <div v-if="pinned && saved && pinned.id === saved.id" class="flex flex-wrap items-center gap-3 rounded-lg border border-accent2/50 p-3 text-sm"><span>{{ t('beats.pinnedBeat', { revision: pinned.revision }) }} · {{ t('beats.currentBeat', { revision: saved.revision }) }}</span><button v-if="pinned.revision !== saved.revision" type="button" class="beat-button" :disabled="busy || dirty" @click="usePinned">{{ t('beats.usePinned') }}</button></div>
    <template v-if="draft && pattern">
      <fieldset :disabled="busy" class="space-y-5 rounded-xl border border-border bg-panel p-4">
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label class="beat-label"><span>{{ t('beats.name') }}</span><input v-model="draft.title" class="beat-input" maxlength="120"></label>
          <label class="beat-label"><span>{{ t('beats.bpm') }}</span><input v-model.number="draft.bpm" type="number" min="20" max="300" step="0.1" class="beat-input"></label>
          <label class="beat-label"><span>{{ t('beats.seed') }}</span><input v-model.number="draft.seed" type="number" min="0" max="2147483647" class="beat-input"></label>
          <label class="beat-label"><span>{{ t('beats.kit') }}</span><select :value="draft.kit_id" class="beat-input" @change="selectKit"><option v-if="!kit" :value="draft.kit_id">{{ draft.kit_id }}</option><option v-for="item in kits" :key="item.sha256" :value="item.manifest.kit_id">{{ item.manifest.kit_id }} · {{ item.manifest.revision }}</option></select></label>
        </div>
        <div class="flex flex-wrap items-center gap-2" role="group" :aria-label="t('beats.pattern', { pattern: patternId })"><button v-for="item in draft.patterns" :key="item.id" type="button" class="beat-button" :aria-pressed="item.id === patternId" :disabled="outsideHits > 0 && item.id !== patternId" :class="item.id === patternId ? 'border-accent2 bg-accent1/15' : ''" @click="patternId = item.id">{{ t('beats.pattern', { pattern: item.id }) }}</button><button type="button" class="beat-button" :disabled="draft.patterns.length >= 4 || outsideHits > 0" @click="addPattern">{{ t('beats.addPattern') }}</button></div>
        <div class="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <label class="beat-label"><span>{{ t('beats.bars') }}</span><select v-model.number="pattern.bars" class="beat-input" @change="resizeGrid"><option v-for="value in 4" :key="value" :value="value">{{ value }}</option></select></label>
          <label class="beat-label"><span>{{ t('beats.numerator') }}</span><input v-model.number="pattern.meter.numerator" class="beat-input" type="number" min="1" max="32" @change="resizeGrid"></label>
          <label class="beat-label"><span>{{ t('beats.denominator') }}</span><select v-model.number="pattern.meter.denominator" class="beat-input" @change="resizeGrid"><option v-for="value in [2,4,8,16]" :key="value" :value="value">{{ value }}</option></select></label>
          <label class="beat-label"><span>{{ t('beats.subdivision') }}</span><select v-model.number="pattern.steps_per_quarter" class="beat-input" @change="resizeGrid"><option v-for="value in [2,3,4,6,8]" :key="value" :value="value">{{ value }}</option></select></label>
          <label class="beat-label"><span>{{ t('beats.swing') }}</span><input v-model.number="draft.global_swing" type="number" min="0.5" max="0.75" step="0.01" class="beat-input"></label>
        </div>
        <p class="text-xs text-text-dim">{{ t('beats.swingHint') }}</p><p v-if="!kit" class="text-sm text-status-warning">{{ t('beats.kitMissing') }}</p>
        <div class="flex flex-wrap gap-2"><button v-for="value in pattern.bars ?? 1" :key="value" type="button" class="beat-button" :aria-pressed="bar === value - 1" @click="bar = value - 1">{{ t('beats.bar', { bar: value }) }}</button></div>
        <div v-if="outsideHits" role="status" class="space-y-2 rounded-lg border border-status-warning/50 p-3 text-sm"><p>{{ t('beats.gridShrink', { count: outsideHits }) }}</p><button type="button" class="beat-button" @click="removeOutsideHits">{{ t('beats.removeOutside', { count: outsideHits }) }}</button></div><p class="text-sm text-text-dim">{{ t('beats.selectStep') }}</p>
        <div class="max-w-full overflow-x-auto rounded-lg border border-border">
          <table class="border-separate border-spacing-1 text-sm"><thead><tr><th class="min-w-36 text-left">{{ t('beats.instrument') }}</th><th v-for="index in indexes" :key="index" scope="col" class="min-w-11 text-xs font-normal text-text-dim">{{ index + 1 }}</th></tr></thead><tbody>
            <tr v-for="lane in pattern.lanes" :key="lane.lane_id"><th scope="row" class="sticky left-0 z-10 max-w-48 bg-panel px-2 text-left font-medium">{{ lane.label }}</th><td v-for="index in indexes" :key="index"><button type="button" class="beat-cell" :aria-label="t('beats.step', { lane: lane.label, step: index + 1 })" :aria-pressed="!!step(lane, index)" :class="step(lane,index) ? 'border-accent2 bg-accent1/50' : index % (pattern.steps_per_quarter ?? 4) === 0 ? 'border-border bg-panel-2' : 'border-border bg-panel'" @click="toggleStep(lane,index,$event.shiftKey)" @keydown.enter.prevent="toggleStep(lane,index,true)" @contextmenu.prevent="inspectStep(lane,index)"><span aria-hidden="true">{{ step(lane,index) ? '●' : '·' }}</span></button></td></tr>
          </tbody></table>
        </div>
        <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('beats.laneSettings') }}</summary><div class="space-y-4"><div v-for="lane in pattern.lanes" :key="lane.lane_id" class="grid gap-3 border-t border-border pt-3 sm:grid-cols-2 lg:grid-cols-5"><span class="text-sm font-semibold">{{ lane.label }}<label class="mt-2 flex min-h-11 items-center gap-2 font-normal"><input v-model="lane.muted" type="checkbox">{{ t('beats.mute') }}</label><label class="flex min-h-11 items-center gap-2 font-normal"><input type="checkbox" :checked="humanizeLanes.includes(lane.lane_id)" @change="toggleHumanize(lane.lane_id,$event)">{{ t('beats.humanizeLane') }}</label></span><label class="beat-label"><span>{{ t('beats.instrument') }}</span><select v-model="lane.instrument_id" class="beat-input"><option v-for="instrument in kit?.manifest.instruments ?? []" :key="instrument.instrument_id" :value="instrument.instrument_id">{{ instrument.label }}</option></select></label><label class="beat-label"><span>{{ t('beats.gain') }}</span><input v-model.number="lane.gain_db" type="number" min="-60" max="12" step="0.5" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.pan') }}</span><input v-model.number="lane.pan" type="number" min="-1" max="1" step="0.1" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.swing') }}</span><input v-model.number="lane.swing" type="number" min="0.5" max="0.75" step="0.01" class="beat-input"></label></div></div></details>
        <section v-if="chosenStep" class="space-y-3 rounded-lg border border-accent2/50 p-4"><div class="flex flex-wrap justify-between gap-2"><h3 class="font-semibold">{{ t('beats.stepDetails') }} · {{ chosenLane?.label }} · {{ chosenStep.index + 1 }}</h3><button type="button" class="beat-button" @click="removeStep">{{ t('beats.removeStep') }}</button></div><div class="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <label class="beat-label"><span>{{ t('beats.velocity') }}</span><input v-model.number="chosenStep.velocity" type="number" min="1" max="127" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.probability') }}</span><input v-model.number="chosenStep.probability" type="number" min="0" max="1" step="0.05" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.offset') }}</span><input v-model.number="chosenStep.offset_steps" type="number" min="-0.5" max="0.5" step="0.01" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.ratchet') }}</span><input v-model.number="chosenStep.ratchet" type="number" min="1" max="4" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.note') }}</span><input v-model.number="chosenStep.note_midi" type="number" min="0" max="127" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.cents') }}</span><input v-model.number="chosenStep.cents" type="number" min="-100" max="100" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.gate') }}</span><input v-model.number="chosenStep.gate_steps" type="number" min="0.05" max="8" step="0.05" class="beat-input"></label>
          <label class="beat-label"><span>{{ t('beats.articulation') }}</span><select v-model="chosenStep.articulation_id" class="beat-input"><option :value="null">{{ t('beats.defaultArticulation') }}</option><option v-for="value in articulations()" :key="value" :value="value">{{ value }}</option></select></label>
        </div><details class="space-y-3"><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('beats.pitchCurve') }}</summary><p class="text-xs text-text-dim">{{ t('beats.pitchHint') }}</p><div v-for="(point,index) in chosenStep.pitch_curve ?? []" :key="index" class="my-2 grid gap-2 sm:grid-cols-3"><label class="beat-label"><span>{{ t('beats.pitchPosition') }}</span><input v-model.number="point.position" type="number" min="0" max="1" step="0.01" :disabled="index === 0 || index === (chosenStep.pitch_curve?.length ?? 0) - 1" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.pitchCents') }}</span><input v-model.number="point.cents" type="number" min="-2400" max="2400" step="1" class="beat-input"></label><button type="button" class="beat-button self-end" :disabled="index === 0 || index === (chosenStep.pitch_curve?.length ?? 0) - 1" @click="chosenStep.pitch_curve?.splice(index,1)">{{ t('beats.removePitchPoint') }}</button></div><div class="flex flex-wrap gap-2"><button type="button" class="beat-button" :disabled="(chosenStep.pitch_curve?.length ?? 0) >= 16" @click="addPitchPoint">{{ t('beats.addPitchPoint') }}</button><button v-if="chosenStep.pitch_curve?.length" type="button" class="beat-button" @click="chosenStep.pitch_curve = []">{{ t('beats.clearPitchCurve') }}</button></div></details></section>
        <div class="flex flex-wrap gap-2"><button type="button" class="beat-button" :disabled="dirty" @click="mutate('variation')">{{ t('beats.vary') }}</button><label class="beat-label"><span>{{ t('beats.humanizeTiming') }}</span><input v-model.number="timingHumanize" type="number" min="0" max="0.1" step="0.01" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.humanizeVelocity') }}</span><input v-model.number="velocityHumanize" type="number" min="0" max="12" class="beat-input"></label><button type="button" class="beat-button" :disabled="dirty || !humanizeLanes.length" @click="mutate('humanize')">{{ t('beats.humanize') }}</button></div><p class="text-xs text-text-dim">{{ t('beats.transformHint') }}</p>
        <section class="space-y-3"><h3 class="font-semibold">{{ t('beats.arrangement') }}</h3><div v-for="(placement,index) in draft.chain" :key="index" class="grid gap-2 sm:grid-cols-4"><label class="beat-label"><span>{{ t('beats.pattern', { pattern: '' }) }}</span><select v-model="placement.pattern_id" class="beat-input"><option v-for="item in draft.patterns" :key="item.id" :value="item.id">{{ item.id }}</option></select></label><label class="beat-label"><span>{{ t('beats.repeats') }}</span><input v-model.number="placement.repeats" type="number" min="1" max="64" class="beat-input"></label><label class="beat-label"><span>{{ t('beats.section') }}</span><input v-model="placement.section_label" maxlength="80" class="beat-input"></label><button type="button" class="beat-button self-end" :disabled="draft.chain.length <= 1" @click="draft.chain.splice(index,1)">{{ t('beats.removeSection') }}</button></div><button type="button" class="beat-button" :disabled="draft.chain.length >= 128" @click="draft.chain.push({ pattern_id: patternId, repeats: 1, section_label: '' })">{{ t('beats.addSection') }}</button></section>
      </fieldset>
      <div class="flex flex-wrap items-center gap-3"><button type="button" class="beat-button border-accent2 bg-accent1/20" :disabled="busy || !dirty || outsideHits > 0" @click="save">{{ t('beats.save') }}</button><button type="button" class="beat-button" :disabled="busy || !dirty" @click="discard">{{ t('beats.discard') }}</button><span class="text-sm text-text-dim">{{ dirty ? t('beats.unsaved') : t('beats.saved', { revision: saved?.revision }) }}</span></div>
      <section class="space-y-3 rounded-xl border border-border bg-panel p-4"><div class="flex flex-wrap gap-2"><button type="button" class="beat-button" :disabled="busy || dirty || !kit" @click="reviewRender('preview')">{{ t('beats.preview') }}</button><button type="button" class="beat-button" :disabled="busy || dirty || !kit" @click="reviewRender('full')">{{ t('beats.render') }}</button></div><label class="flex min-h-11 items-center gap-2 text-sm"><input v-model="approximate" type="checkbox" :disabled="busy">{{ t('beats.approximate') }}</label><p class="text-xs text-text-dim">{{ t('beats.midiHint') }}</p><div v-if="estimate && renderRequest" class="space-y-2 rounded-lg border border-border p-3 text-sm"><h3 class="font-semibold">{{ t('beats.renderReview') }} · {{ t(renderRequest.mode === 'full' ? 'beats.full' : 'beats.patternOnly') }}</h3><p>{{ t('beats.duration', { seconds: estimate.duration_sec.toFixed(2) }) }} · {{ t('beats.required', { size: shortSize(estimate.required_free_bytes) }) }}</p><p>{{ t('beats.available', { size: shortSize(estimate.free_bytes) }) }} · {{ t('beats.temporary', { size: shortSize(estimate.temporary_bytes) }) }}</p><p v-if="!estimate.can_start">{{ t('beats.diskUnavailable') }}</p><button type="button" class="beat-button" :disabled="busy || !estimate.can_start || !revisionMatches" @click="startRender">{{ t('beats.confirmRender') }}</button></div></section>
      <details class="rounded-lg border border-border p-4"><summary class="min-h-11 cursor-pointer py-2 text-sm font-semibold">{{ t('beats.revisions') }}</summary><p class="text-xs text-text-dim">{{ t('beats.archiveHint') }}</p><button type="button" class="beat-button my-3" :disabled="busy || dirty" @click="archive">{{ t('beats.archiveBeat') }}</button><p class="mb-3 text-xs text-text-dim">{{ t('beats.restoreHint') }}</p><div v-for="version in revisions" :key="version.revision" class="flex flex-wrap items-center justify-between gap-2 border-t border-border py-2 text-sm"><span>{{ t('beats.revision', { revision: version.revision }) }} · {{ version.created_at }}</span><button type="button" class="beat-button" :disabled="busy || dirty || version.revision === saved?.revision" @click="mutate('restore',version.revision)">{{ t('beats.restore') }}</button></div></details>
      <section class="space-y-3"><div class="flex flex-wrap items-center justify-between gap-3"><h3 class="font-semibold">{{ t('beats.history') }}</h3><button type="button" class="beat-button" @click="refreshJobs">{{ t('beats.refresh') }}</button></div><p class="text-xs text-text-dim">{{ t('beats.ongoing') }}</p><p v-if="!jobs.length" class="text-sm text-text-dim">{{ t('beats.empty') }}</p><article v-for="job in jobs" :key="job.id" class="space-y-3 rounded-xl border border-border bg-panel p-4"><div class="flex flex-wrap items-center justify-between gap-3"><span class="text-sm font-semibold">{{ t('beats.revision', { revision: job.beat_revision }) }} · {{ t(`beats.statuses.${job.status}`) }} · {{ t(job.request.mode === 'full' ? 'beats.full' : 'beats.patternOnly') }}</span><button v-if="activeJob(job)" type="button" class="beat-button" :disabled="busy" @click="jobAction(job,'cancel')">{{ t('beats.cancel') }}</button><button v-if="job.error_code === 'beat_save_failed'" type="button" class="beat-button" :disabled="busy" @click="jobAction(job,'save')">{{ t('beats.retrySave') }}</button></div><p v-if="job.error_code" role="status" class="text-sm text-status-warning">{{ t(`beats.errors.${job.error_code}`) }}</p><progress v-if="job.total_frames" class="w-full" :value="job.progress_frames ?? 0" :max="job.total_frames" :aria-label="t('beats.statuses.running')"/><p class="text-xs text-text-dim">{{ t('beats.elapsed', { seconds: elapsed(job) }) }}<template v-if="activeJob(job)"> · {{ eta(job) === null ? t('beats.etaUnknown') : t('beats.eta', { seconds: eta(job) }) }}</template></p><SpeechAudioPreview v-if="fileUrl(job,'mix')" :src="fileUrl(job,'mix') ?? ''" :label="t('beats.full')" preload="none" /><div v-if="job.status === 'done' && job.request.mode === 'full'" class="flex flex-wrap gap-3"><button v-if="!editorProjects[job.id]" type="button" class="beat-button" :disabled="busy" @click="prepareEditor(job)">{{ t('beats.prepareEditor') }}</button><RouterLink v-else :to="`/editor/${editorProjects[job.id]}`" class="beat-button inline-flex items-center text-accent2">{{ t('beats.openEditor') }}</RouterLink><RouterLink to="/music" class="beat-button inline-flex items-center text-accent2" @click="createSong(job)">{{ t('songFromBeat.title') }}</RouterLink><button v-if="job.manifest" type="button" class="beat-button" :disabled="busy" @click="useReference(job)">{{ t('beats.useReference') }}</button><RouterLink v-if="referenceJob === job.id" to="/music/stable-audio3" class="beat-button inline-flex items-center text-accent2">{{ t('beats.openReference') }}</RouterLink><RouterLink v-if="referenceJob === job.id" to="/music" class="beat-button inline-flex items-center text-accent2">{{ t('beats.openQueueReference') }}</RouterLink><p v-if="referenceJob === job.id" class="text-xs text-text-dim">{{ t('beats.referenceHint') }}</p></div><div v-if="job.manifest" class="space-y-3"><p v-if="job.manifest.peak > 1" role="status" class="text-sm text-status-warning">{{ t('beats.clipWarning') }}</p><p v-if="job.manifest.peak === 0" class="text-sm text-status-warning">{{ t('beats.silentWarning') }}</p><div class="flex flex-wrap gap-3"><a v-for="artifact in job.manifest.artifacts" :key="artifact.id" class="beat-button inline-flex items-center text-accent2" :href="beatArtifactUrl(job.id,artifact.id)" :download="artifact.filename">{{ t('beats.download', { kind: artifact.filename }) }}</a></div><details><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('beats.reviewManifest') }}</summary><p class="break-all text-xs text-text-dim">{{ job.manifest.document_sha256 }} · {{ job.manifest.algorithm_version }} · {{ t('beats.peak', { peak: job.manifest.peak.toFixed(3) }) }}</p><p v-for="warning in job.manifest.warnings ?? []" :key="warning" class="text-xs text-status-warning">{{ t(`beats.warnings.${warning}`) }}</p></details></div></article></section>
    </template>
    <details class="rounded-xl border border-border bg-panel p-4"><summary class="min-h-11 cursor-pointer py-2 text-sm font-semibold">{{ t('beats.importKit') }}</summary><p class="my-3 text-sm text-text-dim">{{ t('beats.importHint') }}</p><div class="grid gap-3 sm:grid-cols-2"><label class="beat-label"><span>{{ t('beats.manifest') }}</span><input type="file" accept="application/json,.json" :disabled="busy" class="beat-input" @change="pickFile($event,'manifest')"></label><label class="beat-label"><span>{{ t('beats.archive') }}</span><input type="file" accept="application/zip,.zip" :disabled="busy" class="beat-input" @change="pickFile($event,'archive')"></label></div><button type="button" class="beat-button mt-3" :disabled="busy || !manifestFile || !archiveFile" @click="importKit">{{ t('beats.import') }}</button></details>
  </div>
</template>

<style scoped>
@reference '../../style.css';
.beat-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2 disabled:cursor-not-allowed disabled:opacity-50; }
.beat-label { @apply flex min-w-0 flex-col gap-1.5 text-xs text-text-dim; }
.beat-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2; }
.beat-cell { @apply h-11 w-11 rounded-md border text-accent2 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent2; }
</style>
