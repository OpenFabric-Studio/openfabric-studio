<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMashupStore } from '../../stores/mashups'
import * as api from '../../api/mashups'
import { mashupArtifactUrl } from '../../api/mashups'
import { parseMashupCreateRequest, parseMashupGeneratePlanRequest, parseMashupJobRequest } from '../../api/generated'
import type { MashupGeneratePlanRequest, MashupJob, MashupJobRequest, MashupSourcePreview, MashupStoragePreview, MusicConditioningSourceInfo, SelectedSource } from '../../api/generated'
import MusicSourcePicker from '../../components/shared/MusicSourcePicker.vue'
import AudioQualityReport from '../../components/shared/AudioQualityReport.vue'
import SpeechAudioPreview from '../voice/SpeechAudioPreview.vue'
import MashupTimingEditor from './MashupTimingEditor.vue'
import MashupPlanEditor from './MashupPlanEditor.vue'

const props = defineProps<{ mashupId?: string }>(), { t } = useI18n(), store = useMashupStore()
const title = ref(''), sourceA = ref<SelectedSource | null>(null), sourceB = ref<SelectedSource | null>(null)
const startA = ref(0), endA = ref(0), startB = ref(0), endB = ref(0), sourceBusyA = ref(false), sourceBusyB = ref(false)
const previews = ref<{ a: MashupSourcePreview; b: MashupSourcePreview } | null>(null), sourceReviewKey = ref(''), sourceSubmission = ref('')
const reviewing = ref(false), localError = ref(false), timingDirtyA = ref(false), timingDirtyB = ref(false), placementsDirty = ref(false)
const orientation = ref<MashupGeneratePlanRequest['orientation']>('a_vocals_b_backing'), outputBpm = ref(120), vocalPitch = ref(0), backingPitch = ref(0), largePitch = ref(false)
const vocalAlias = ref<NonNullable<MashupGeneratePlanRequest['vocal_tempo_alias']>>('declared'), backingAlias = ref<NonNullable<MashupGeneratePlanRequest['backing_tempo_alias']>>('declared')
const renderMode = ref<'preview' | 'full'>('preview'), previewStart = ref(0), previewLength = ref(15)
const renderReview = ref<{ request: MashupJobRequest; forecast: MashupStoragePreview } | null>(null), renderConfirmed = ref(false)
const projectIds = ref<Record<string, number>>({}), publishing = ref(false), now = ref(Date.now())
let alive = true, generation = 0, controller: AbortController | undefined, clock: ReturnType<typeof setInterval> | undefined
const busy = computed(() => store.busy || reviewing.value || publishing.value || sourceBusyA.value || sourceBusyB.value)
const sourceKey = computed(() => JSON.stringify([title.value.trim(), sourceA.value, startA.value, endA.value, sourceB.value, startB.value, endB.value]))
watch(() => JSON.stringify(sourceA.value), () => { startA.value = 0; endA.value = 0 }, { flush: 'sync' })
watch(() => JSON.stringify(sourceB.value), () => { startB.value = 0; endB.value = 0 }, { flush: 'sync' })
const sourceRequests = computed(() => {
  if (sourceA.value?.source.kind !== 'version' || sourceB.value?.source.kind !== 'version' || sourceA.value.source.track_id === sourceB.value.source.track_id && sourceA.value.source.version_id === sourceB.value.source.version_id) return null
  const a = { source: sourceA.value.source, excerpt_start_sec: startA.value, excerpt_end_sec: endA.value }, b = { source: sourceB.value.source, excerpt_start_sec: startB.value, excerpt_end_sec: endB.value }
  if ([a, b].some(item => !Number.isFinite(item.excerpt_start_sec) || !Number.isFinite(item.excerpt_end_sec) || item.excerpt_start_sec < 0 || item.excerpt_end_sec <= item.excerpt_start_sec || item.excerpt_end_sec - item.excerpt_start_sec > 600)) return null
  return { a, b }
})
const combinedSourceBudget = computed(() => {
  if (!previews.value) return null
  const { a, b } = previews.value, free = a.free_bytes == null || b.free_bytes == null ? null : Math.min(a.free_bytes, b.free_bytes)
  return { required: a.required_free_bytes + b.required_free_bytes, free }
})
const sourcesReviewed = computed(() => !!sourceRequests.value && sourceReviewKey.value === sourceKey.value && previews.value?.a.can_start && previews.value.b.can_start && previews.value.a.separator_ready && previews.value.b.separator_ready && combinedSourceBudget.value?.free != null && combinedSourceBudget.value.free >= combinedSourceBudget.value.required)
const settings = computed(() => {
  const doc = store.document, vocal = orientation.value === 'a_vocals_b_backing' ? doc?.source_a : doc?.source_b, backing = orientation.value === 'a_vocals_b_backing' ? doc?.source_b : doc?.source_a
  if (!doc || !vocal?.timing || !backing?.timing || JSON.stringify(vocal.timing.meter) !== JSON.stringify(backing.timing.meter)) return null
  try { return parseMashupGeneratePlanRequest({ expected_revision: doc.revision, orientation: orientation.value, target_bpm: outputBpm.value, meter: vocal.timing.meter, target_key: backing.timing.key ?? { status: 'unknown' }, vocal_pitch_semitones: vocalPitch.value, backing_pitch_semitones: backingPitch.value, allow_large_pitch_shift: largePitch.value, vocal_tempo_alias: vocalAlias.value, backing_tempo_alias: backingAlias.value, locks: doc.plan?.placements.filter(item => item.locked).map(item => ({ placement_id: item.id, target_start_tick: item.target_start_tick })) ?? [] }) } catch { return null }
})
const changedSettings = computed(() => {
  const saved = store.document?.plan?.settings
  return !!saved && JSON.stringify([orientation.value, outputBpm.value, vocalPitch.value, backingPitch.value, largePitch.value, vocalAlias.value, backingAlias.value]) !== JSON.stringify([saved.orientation, saved.target_bpm, saved.vocal_pitch_semitones ?? 0, saved.backing_pitch_semitones ?? 0, saved.allow_large_pitch_shift ?? false, saved.vocal_tempo_alias ?? 'declared', saved.backing_tempo_alias ?? 'declared'])
})
const renderKey = computed(() => JSON.stringify([store.document?.id, store.document?.revision, renderMode.value, previewStart.value, previewLength.value, timingDirtyA.value, timingDirtyB.value, placementsDirty.value, changedSettings.value]))
const cleanPlan = computed(() => !!store.document?.plan && !timingDirtyA.value && !timingDirtyB.value && !placementsDirty.value && !changedSettings.value)
watch(sourceKey, () => { invalidate(); previews.value = null; sourceReviewKey.value = ''; sourceSubmission.value = '' })
watch(renderKey, () => { invalidate(); renderReview.value = null; renderConfirmed.value = false })
watch(() => JSON.stringify([store.document?.id, store.document?.plan?.id, store.document?.plan?.revision]), () => {
  const value = store.document?.plan?.settings
  if (value) { orientation.value = value.orientation; outputBpm.value = value.target_bpm; vocalPitch.value = value.vocal_pitch_semitones ?? 0; backingPitch.value = value.backing_pitch_semitones ?? 0; largePitch.value = value.allow_large_pitch_shift ?? false; vocalAlias.value = value.vocal_tempo_alias ?? 'declared'; backingAlias.value = value.backing_tempo_alias ?? 'declared' }
  placementsDirty.value = false
})
function invalidate() { ++generation; controller?.abort(); controller = undefined; reviewing.value = false; localError.value = false }
function bytes(value?: number | null) { return value == null ? t('mashups.unknown') : `${(value / 1024 ** 2).toFixed(1)} MiB` }
function inspected(side: 'a' | 'b', info: MusicConditioningSourceInfo | null) { if (info) { const selected = side === 'a' ? endA : endB; if (!selected.value) selected.value = Math.min(600, info.duration_sec) } }
async function reviewSources() {
  const current = sourceRequests.value
  if (!current || busy.value || store.document) return
  invalidate(); const token = generation, key = sourceKey.value, request = new AbortController(); controller = request; reviewing.value = true
  try { const [a, b] = await Promise.all([api.previewMashupSource(current.a, request.signal), api.previewMashupSource(current.b, request.signal)]); if (alive && token === generation && key === sourceKey.value && !request.signal.aborted) { if (a.sha256 !== sourceA.value?.audio_sha256 || b.sha256 !== sourceB.value?.audio_sha256) throw new Error('Changed source'); previews.value = { a, b }; sourceReviewKey.value = key; sourceSubmission.value ||= crypto.randomUUID().replaceAll('-', '') } }
  catch { if (alive && token === generation && !request.signal.aborted) localError.value = true }
  finally { if (alive && token === generation) { reviewing.value = false; controller = undefined } }
}
async function prepare() {
  if (!sourcesReviewed.value || !sourceRequests.value || !previews.value || busy.value || !title.value.trim()) return
  const request = parseMashupCreateRequest({ title: title.value.trim(), submission_key: sourceSubmission.value, source_a: { ...sourceRequests.value.a, sha256: previews.value.a.sha256 }, source_b: { ...sourceRequests.value.b, sha256: previews.value.b.sha256 }, separation_quality: 'fast' })
  await store.create(request)
}
function newDraft() { if (busy.value || store.active) return; store.newDraft(); invalidate(); title.value = ''; sourceA.value = null; sourceB.value = null; startA.value = startB.value = endA.value = endB.value = 0; timingDirtyA.value = timingDirtyB.value = placementsDirty.value = false }
function choose(event: Event) { if (event.target instanceof HTMLSelectElement && event.target.value) { invalidate(); timingDirtyA.value = timingDirtyB.value = placementsDirty.value = false; void store.load(event.target.value) } }
async function generate() { if (!settings.value || busy.value || store.active || timingDirtyA.value || timingDirtyB.value || placementsDirty.value) return; await store.generate(settings.value) }
function renderRequest(): MashupJobRequest | null {
  const doc = store.document
  if (!doc?.plan || !cleanPlan.value) return null
  const seconds = doc.plan.duration_ticks * 60 / (960 * doc.plan.settings.target_bpm)
  if (renderMode.value === 'preview' && (!Number.isFinite(previewStart.value) || !Number.isFinite(previewLength.value) || previewStart.value < 0 || previewLength.value <= 0 || previewLength.value > 60 || previewStart.value >= seconds)) return null
  try { return parseMashupJobRequest({ expected_revision: doc.revision, submission_key: crypto.randomUUID().replaceAll('-', ''), mode: renderMode.value, ...(renderMode.value === 'preview' ? { preview_start_tick: Math.round(previewStart.value * doc.plan.settings.target_bpm * 960 / 60), preview_end_tick: Math.min(doc.plan.duration_ticks, Math.round((previewStart.value + previewLength.value) * doc.plan.settings.target_bpm * 960 / 60)) } : {}) }) } catch { return null }
}
async function reviewRender() {
  const doc = store.document, body = renderRequest()
  if (!doc || !body || busy.value || store.active) return
  invalidate(); renderReview.value = null; renderConfirmed.value = false; const token = generation, key = renderKey.value, request = new AbortController(); controller = request; reviewing.value = true
  try { const forecast = await api.previewMashupStorage(doc.id, body, request.signal); if (alive && token === generation && key === renderKey.value && !request.signal.aborted) renderReview.value = { request: body, forecast } }
  catch { if (alive && token === generation && !request.signal.aborted) localError.value = true }
  finally { if (alive && token === generation) { reviewing.value = false; controller = undefined } }
}
async function render() { if (!renderReview.value?.forecast.can_start || !renderReview.value.forecast.stretch_ready || !renderConfirmed.value || !cleanPlan.value || busy.value || store.active) return; await store.render(renderReview.value.request) }
async function editor(job: MashupJob) {
  if (busy.value || job.status !== 'done' || job.kind !== 'full') return
  invalidate(); const token = generation, request = new AbortController(); controller = request; publishing.value = true
  try { const result = await api.publishMashupProject(job, request.signal); if (alive && token === generation && !request.signal.aborted) projectIds.value[job.id] = result.project_id }
  catch { if (alive && token === generation && !request.signal.aborted) localError.value = true }
  finally { if (alive && token === generation) { publishing.value = false; controller = undefined } }
}
function mixUrl(job: MashupJob) { const artifact = job.manifest?.artifacts.find(item => item.kind === 'mix'); return artifact ? mashupArtifactUrl(job.id, artifact.id) : null }
function isActive(job: MashupJob) { return ['queued', 'running', 'saving', 'cancelling'].includes(job.status) }
function elapsed(job: MashupJob) { return Math.max(0, Math.round(((job.finished_at ? Date.parse(job.finished_at) : now.value) - Date.parse(job.started_at ?? job.created_at)) / 1000)) }
onMounted(() => { void store.start(props.mashupId); clock = setInterval(() => { if (store.active) now.value = Date.now() }, 1000) })
watch(() => props.mashupId, id => { if (alive) { invalidate(); void store.start(id) } })
onBeforeUnmount(() => { alive = false; invalidate(); if (clock !== undefined) clearInterval(clock); store.stop() })
</script>

<template>
  <div class="min-w-0 space-y-6" :aria-busy="busy">
    <header class="space-y-3"><h2 class="text-xl font-semibold text-text">{{ t('mashups.title') }}</h2><p class="max-w-3xl text-sm leading-relaxed text-text-dim">{{ t('mashups.intro') }}</p>
      <div class="flex flex-wrap items-end gap-3"><label class="mashup-label flex-1">{{ t('mashups.saved') }}<select class="mashup-input" :value="store.document?.id ?? ''" :disabled="busy" @change="choose"><option value="">—</option><option v-for="doc in store.documents" :key="doc.id" :value="doc.id">{{ doc.title }} · {{ doc.updated_at }}</option></select></label><button type="button" class="mashup-button" :disabled="busy || store.active" @click="newDraft">{{ t('mashups.new') }}</button><button type="button" class="mashup-button" :disabled="busy" @click="store.refresh">{{ t('mashups.reload') }}</button></div>
    </header>
    <p v-if="store.disconnected" role="status" class="rounded-lg border border-status-warning p-3 text-sm text-status-warning">{{ t('mashups.disconnected') }}</p>
    <p v-if="store.error || localError" role="alert" class="text-sm text-status-failed">{{ t(`mashups.${store.error || 'actionFailed'}`) }}</p>
    <section v-if="!store.document" class="space-y-4 rounded-xl border border-border bg-panel p-5">
      <label class="mashup-label">{{ t('mashups.name') }}<input v-model="title" maxlength="120" class="mashup-input" :disabled="busy" /></label><p class="text-sm leading-relaxed text-text-dim">{{ t('mashups.sourceHelp') }}</p>
      <div class="grid gap-5 lg:grid-cols-2"><div class="space-y-3"><MusicSourcePicker v-model="sourceA" :label="t('mashups.source', { side: 'A' })" :disabled="store.busy || reviewing || publishing || sourceBusyB" @busy="sourceBusyA = $event" @inspected="inspected('a', $event)" /><label class="mashup-label">{{ t('mashups.excerptStart') }}<input v-model.number="startA" type="number" min="0" max="21600" step="any" class="mashup-input" :disabled="busy" /></label><label class="mashup-label">{{ t('mashups.excerptEnd') }}<input v-model.number="endA" type="number" min="0" max="21600" step="any" class="mashup-input" :disabled="busy" /></label></div><div class="space-y-3"><MusicSourcePicker v-model="sourceB" :label="t('mashups.source', { side: 'B' })" :disabled="store.busy || reviewing || publishing || sourceBusyA" @busy="sourceBusyB = $event" @inspected="inspected('b', $event)" /><label class="mashup-label">{{ t('mashups.excerptStart') }}<input v-model.number="startB" type="number" min="0" max="21600" step="any" class="mashup-input" :disabled="busy" /></label><label class="mashup-label">{{ t('mashups.excerptEnd') }}<input v-model.number="endB" type="number" min="0" max="21600" step="any" class="mashup-input" :disabled="busy" /></label></div></div>
      <div class="flex flex-wrap gap-3"><button type="button" class="mashup-button" :disabled="busy || !sourceRequests" @click="reviewSources">{{ t('mashups.inspect') }}</button><button type="button" class="mashup-button bg-accent1 text-white" :disabled="busy || !sourcesReviewed || !title.trim()" @click="prepare">{{ t('mashups.prepare') }}</button></div>
      <p v-if="combinedSourceBudget" class="text-sm text-text-dim">{{ t('mashups.totalForecast', { required: bytes(combinedSourceBudget.required), free: bytes(combinedSourceBudget.free) }) }}</p>
      <template v-if="previews"><div v-for="(preview, side) in previews" :key="side" class="space-y-2 rounded-lg border border-border p-3"><h3 class="text-sm font-medium">{{ t('mashups.source', { side: side.toUpperCase() }) }}</h3><p class="text-sm text-text-dim">{{ t('mashups.forecast', { required: bytes(preview.required_free_bytes), free: bytes(preview.free_bytes) }) }}</p><p class="break-all text-xs text-text-dim">{{ t('mashups.hash') }}: {{ preview.sha256 }}</p><p v-if="!preview.separator_ready" role="status" class="text-sm text-status-warning">{{ t('mashups.separatorMissing') }}</p></div></template>
    </section>
    <template v-else>
      <section class="space-y-3 rounded-xl border border-border bg-panel p-4"><h3 class="font-semibold text-text">{{ store.document.title }}</h3><p class="text-sm text-text-dim">{{ t('mashups.preparingHelp') }}</p><p v-if="store.document.status === 'preparing'" role="status" class="text-sm">{{ t('mashups.preparing') }}</p><button v-if="store.document.status === 'preparing'" type="button" class="mashup-button" :disabled="busy" @click="store.cancelPreparation">{{ t('mashups.cancelPreparation') }}</button><button v-if="['failed', 'cancelled', 'interrupted'].includes(store.document.status)" type="button" class="mashup-button" :disabled="busy || store.active" @click="store.retryPreparation">{{ t('mashups.retryPreparation') }}</button><details><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('mashups.render.details') }}</summary><p v-for="source in [store.document.source_a, store.document.source_b]" :key="source.side" class="break-all py-2 text-xs text-text-dim">{{ source.side.toUpperCase() }} · {{ source.input.excerpt_start_sec }}–{{ source.input.excerpt_end_sec }} s · {{ source.input.sha256 }} · {{ source.preparation_recipe }} · {{ source.music_evidence.restriction }}</p></details></section>
      <div v-if="store.document.status === 'ready'" class="grid items-start gap-4 xl:grid-cols-2"><MashupTimingEditor :source="store.document.source_a" :disabled="busy || store.active" @save="store.timing('a', $event)" @dirty="timingDirtyA = $event" /><MashupTimingEditor :source="store.document.source_b" :disabled="busy || store.active" @save="store.timing('b', $event)" @dirty="timingDirtyB = $event" /></div>
      <section v-if="store.document.status === 'ready'" class="space-y-4 rounded-xl border border-border bg-panel p-5"><h3 class="font-semibold text-text">{{ t('mashups.plan.title') }}</h3><p class="text-sm leading-relaxed text-text-dim">{{ t('mashups.plan.help') }}</p><p v-if="!settings" class="text-sm text-status-warning">{{ t('mashups.plan.missingTiming') }}</p><p v-if="timingDirtyA || timingDirtyB" class="text-sm text-status-warning">{{ t('mashups.plan.dirtyTiming') }}</p>
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><label class="mashup-label">{{ t('mashups.plan.orientation') }}<select v-model="orientation" class="mashup-input" :disabled="busy || store.active"><option value="a_vocals_b_backing">{{ t('mashups.plan.a_vocals_b_backing') }}</option><option value="b_vocals_a_backing">{{ t('mashups.plan.b_vocals_a_backing') }}</option></select></label><label class="mashup-label">{{ t('mashups.plan.bpm') }}<input v-model.number="outputBpm" type="number" min="20" max="300" step="any" class="mashup-input" :disabled="busy || store.active" /></label><label v-for="side in ['vocal', 'backing']" :key="side" class="mashup-label">{{ t(`mashups.plan.${side}Alias`) }}<select v-if="side === 'vocal'" v-model="vocalAlias" class="mashup-input" :disabled="busy || store.active"><option v-for="value in ['declared', 'half', 'double']" :key="value" :value="value">{{ t(`mashups.plan.${value}`) }}</option></select><select v-else v-model="backingAlias" class="mashup-input" :disabled="busy || store.active"><option v-for="value in ['declared', 'half', 'double']" :key="value" :value="value">{{ t(`mashups.plan.${value}`) }}</option></select></label><label class="mashup-label">{{ t('mashups.plan.vocalPitch') }}<input v-model.number="vocalPitch" type="number" min="-12" max="12" step="0.1" class="mashup-input" :disabled="busy || store.active" /></label><label class="mashup-label">{{ t('mashups.plan.backingPitch') }}<input v-model.number="backingPitch" type="number" min="-12" max="12" step="0.1" class="mashup-input" :disabled="busy || store.active" /></label></div>
        <label class="flex min-h-11 items-center gap-3 text-sm"><input v-model="largePitch" type="checkbox" class="size-5 accent-accent1" :disabled="busy || store.active" />{{ t('mashups.plan.largePitch') }}</label><button type="button" class="mashup-button" :disabled="busy || store.active || !settings || timingDirtyA || timingDirtyB || placementsDirty" @click="generate">{{ t('mashups.plan.generate') }}</button>
        <div class="grid gap-3 md:grid-cols-2"><article v-for="candidate in store.document.candidates ?? []" :key="candidate.orientation" class="space-y-2 rounded-lg border border-border p-3"><h4 class="text-sm font-medium">{{ t(`mashups.plan.${candidate.orientation}`) }}</h4><p class="text-sm text-text-dim">{{ candidate.score == null ? t('mashups.plan.noScore') : t('mashups.plan.score', { score: candidate.score.toFixed(1) }) }}</p><p v-for="component in ['key', 'tempo', 'section_fit', 'energy_clash'] as const" :key="component" class="text-xs text-text-dim">{{ t('mashups.plan.component', { name: t(`mashups.plan.components.${component}`), score: candidate[component].score == null ? t('mashups.unknown') : candidate[component].score.toFixed(1) }) }}</p></article></div>
      </section>
      <p v-if="changedSettings" class="text-sm text-status-warning">{{ t('mashups.plan.unsavedSettings') }}</p>
      <MashupPlanEditor v-if="store.document.plan" :plan="store.document.plan" :disabled="busy || store.active" @save="store.savePlacements" @dirty="placementsDirty = $event" />
      <section v-if="store.document.plan" class="space-y-4 rounded-xl border border-border bg-panel p-5"><h3 class="font-semibold text-text">{{ t('mashups.render.title') }}</h3><p class="text-sm text-text-dim">{{ t('mashups.render.noSilentFix') }}</p><div class="flex flex-wrap gap-3"><label class="mashup-label flex-1"><select v-model="renderMode" class="mashup-input" :aria-label="t('mashups.render.title')" :disabled="busy || store.active"><option value="preview">{{ t('mashups.render.preview') }}</option><option value="full">{{ t('mashups.render.full') }}</option></select></label><label v-if="renderMode === 'preview'" class="mashup-label">{{ t('mashups.render.start') }}<input v-model.number="previewStart" type="number" min="0" step="any" class="mashup-input" :disabled="busy || store.active" /></label><label v-if="renderMode === 'preview'" class="mashup-label">{{ t('mashups.render.length') }}<input v-model.number="previewLength" type="number" min="0.1" max="60" step="any" class="mashup-input" :disabled="busy || store.active" /></label></div><button type="button" class="mashup-button" :disabled="busy || store.active || !cleanPlan" @click="reviewRender">{{ t('mashups.render.review') }}</button>
        <template v-if="renderReview"><p class="text-sm text-text-dim">{{ t('mashups.render.required', { required: bytes(renderReview.forecast.required_free_bytes), temporary: bytes(renderReview.forecast.temporary_bytes), free: bytes(renderReview.forecast.free_bytes) }) }}</p><p v-if="!renderReview.forecast.stretch_ready" role="status" class="text-sm text-status-warning">{{ t('mashups.render.stretchMissing') }}</p><label class="flex min-h-11 items-center gap-3 text-sm"><input v-model="renderConfirmed" type="checkbox" data-render-confirm class="size-5 accent-accent1" :disabled="busy || !renderReview.forecast.can_start || !renderReview.forecast.stretch_ready" />{{ t('mashups.render.confirm') }}</label><button type="button" class="mashup-button bg-accent1 text-white" :disabled="busy || store.active || !renderConfirmed || !renderReview.forecast.can_start || !renderReview.forecast.stretch_ready || !cleanPlan" @click="render">{{ t(renderMode === 'preview' ? 'mashups.render.preview' : 'mashups.render.full') }}</button></template>
      </section>
      <section class="space-y-4"><h3 class="font-semibold text-text">{{ t('mashups.render.history') }}</h3><p v-if="!store.jobs.length" class="text-sm text-text-dim">{{ t('mashups.render.noJobs') }}</p><article v-for="job in store.jobs" :key="job.id" class="space-y-3 rounded-xl border border-border bg-panel p-4"><div class="flex flex-wrap items-center justify-between gap-3"><h4 class="text-sm font-semibold">{{ t(job.kind === 'preparation' ? 'mashups.render.preparation' : job.kind === 'preview' ? 'mashups.render.previewJob' : 'mashups.render.fullJob') }} · {{ t(`mashups.render.${job.status}`) }}</h4><button v-if="isActive(job) && job.kind !== 'preparation'" type="button" class="mashup-button" :disabled="busy" @click="store.jobAction(job.id, 'cancel')">{{ t('mashups.render.cancel') }}</button><button v-if="job.error?.code === 'mashup_save_failed'" type="button" class="mashup-button" :disabled="busy" @click="store.jobAction(job.id, 'save')">{{ t('mashups.render.retrySave') }}</button></div><p class="text-sm text-text-dim">{{ t(`mashups.phases.${job.progress.phase}`) }} · {{ elapsed(job) }} s<template v-if="isActive(job)"> · {{ t('mashups.render.pending') }}</template></p><progress v-if="job.progress.total" class="w-full" :max="job.progress.total" :value="job.progress.completed ?? 0" :aria-label="t(`mashups.phases.${job.progress.phase}`)" /><p v-if="job.error" role="status" class="text-sm text-status-warning">{{ t('mashups.actionFailed') }}</p><SpeechAudioPreview v-if="mixUrl(job)" :src="mixUrl(job) ?? ''" :label="t('mashups.render.fullJob')" preload="none" /><template v-if="job.manifest"><AudioQualityReport :metrics="job.manifest.metrics" /><p v-if="job.manifest.track_id" class="text-sm text-text-dim">{{ t('mashups.render.track', { id: job.manifest.track_id }) }}</p><div v-if="job.kind === 'full' && job.status === 'done'" class="flex flex-wrap gap-3"><RouterLink v-if="projectIds[job.id] || job.manifest.project_id" :to="`/editor/${projectIds[job.id] || job.manifest.project_id}`" class="mashup-button inline-flex items-center text-accent2">{{ t('mashups.render.openEditor') }}</RouterLink><button v-else type="button" class="mashup-button" :disabled="busy" @click="editor(job)">{{ t('mashups.render.editor') }}</button></div><details><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('mashups.render.details') }}</summary><p class="break-all text-xs text-text-dim">{{ job.manifest.plan_sha256 }} · {{ job.manifest.source_a_sha256 }} · {{ job.manifest.source_b_sha256 }}</p><p v-for="warning in job.manifest.warnings ?? []" :key="warning" class="py-1 text-xs text-status-warning">{{ t(`mashups.warnings.${warning}`) }}</p><div class="mt-3 flex flex-wrap gap-3"><a v-for="artifact in job.manifest.artifacts" :key="artifact.id" :href="mashupArtifactUrl(job.id, artifact.id)" :download="artifact.filename" class="mashup-button inline-flex items-center text-accent2">{{ t('mashups.render.download', { filename: artifact.filename }) }}</a></div></details></template></article></section>
    </template>
  </div>
</template>
<style scoped>
@reference "../../style.css";
.mashup-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.mashup-label { @apply flex min-w-0 flex-col gap-2 text-sm text-text-dim; }
.mashup-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
