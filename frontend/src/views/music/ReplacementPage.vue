<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useReplacementStore } from '../../stores/replacement'
import * as api from '../../api/replacement'
import { listVoices, type VoiceProfile } from '../../api/voices'
import { parseReplacementFitSettings, parseReplacementMixSettings } from '../../api/generated'
import type { AlignmentEditRequest, AlignmentResult, EqBand, PitchRecommendation, ReplacementMixSettings, ReplacementPlanResponse, ReplacementStoragePreview, SelectedSource } from '../../api/generated'
import MusicSourcePicker from '../../components/shared/MusicSourcePicker.vue'
import AudioQualityReport from '../../components/shared/AudioQualityReport.vue'
import SpeechAudioPreview from '../voice/SpeechAudioPreview.vue'
import ReplacementAlignmentEditor from './ReplacementAlignmentEditor.vue'
import ReplacementMixControls from './ReplacementMixControls.vue'
import ReplacementDiagnosticsCard from './ReplacementDiagnosticsCard.vue'
import RvcModelLibrary from './RvcModelLibrary.vue'

const props = defineProps<{ planId?: string }>()
const { t, te } = useI18n(), store = useReplacementStore()
const step = ref(1), heading = ref<HTMLElement | null>(null)
const mode = ref<api.ReplacementRequest['mode']>('prepared'), title = ref('')
const source = ref<SelectedSource | null>(null), prepared = ref<SelectedSource | null>(null)
const sourceBusy = ref(false), preparedBusy = ref(false), voices = ref<VoiceProfile[]>([]), voicesFailed = ref(false)
const voiceId = ref(''), modelId = ref('trained')
const targetEngine = ref<'seed-vc' | 'rvc'>('seed-vc'), rvcModelId = ref('')
const rvcModelReady = ref(false)
const separator = ref<NonNullable<api.ReplacementRequest['separator']>>('high')
const fit = ref(parseReplacementFitSettings({ max_stretch: 1.25, pitch_shift_semitones: 0, pitch_nudge_semitones: 0, cleanup: 'off', keep_breaths: true }))
const mix = ref<ReplacementMixSettings>(parseReplacementMixSettings({ vocal_level: 'match_original', manual_gain_db: 0, eq_bands: [], compression: null, loudness: { profile: 'off' }, master_level: 'off', space: 'off', ir_asset_id: null, wet: 0 }))
const mixValid = ref(true), previewStart = ref(0), previewLength = ref(15)
const baseline = ref<ReplacementPlanResponse | null>(null), draftConflict = ref(false), alignmentDirty = ref(false), alignmentEpoch = ref(0)
const alignmentInput = ref<AlignmentResult | null>(null), alignmentReviewed = ref<string[]>([]), alignmentEditor = ref<InstanceType<typeof ReplacementAlignmentEditor> | null>(null)
let ownSave: { kind: 'request' | 'mix' | 'alignment'; planId: string; revision: number } | null = null
const storage = ref<ReplacementStoragePreview | null>(null), storageKey = ref(''), reviewing = ref(false), reviewError = ref(false)
const voice = computed(() => voices.value.find(item => item.id === voiceId.value))
const selectedModels = computed(() => voice.value?.models ?? [])
const busy = computed(() => store.busy || !!store.pendingAction || sourceBusy.value || preparedBusy.value || reviewing.value)
const diagnosticNotice = ref('')
const diagnosticsReady = computed(() => !!store.plan?.diagnostics && store.plan.diagnostics.revision === (store.plan.revision ?? 1)
  && !busy.value && !store.active && !store.disconnected && !draftConflict.value && !dirty.value && !mixDirty.value && !alignmentDirty.value)
let alive = true, reviewGeneration = 0, reviewController: AbortController | undefined, voiceController: AbortController | undefined
function key(value: unknown) { return JSON.stringify(value, (_name: string, item: unknown) => item !== null && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item) }
const request = computed(() => {
  if (!source.value || mode.value !== 'convert' && !prepared.value) return null
  if (mode.value !== 'convert' && source.value.audio_sha256 === prepared.value?.audio_sha256) return null
  const pitch = (fit.value.pitch_shift_semitones ?? 0) + (fit.value.pitch_nudge_semitones ?? 0)
  if (pitch < -12 || pitch > 12) return null
  const body = { schema_version: 1, title: title.value.trim(), source: source.value, separator: separator.value, fit: fit.value }
  try {
    return api.replacementRequest(mode.value === 'prepared' ? { ...body, mode: 'prepared', prepared: prepared.value }
      : { ...body, mode: mode.value, ...(mode.value === 'hybrid' ? { prepared: prepared.value } : {}), target: targetEngine.value === 'seed-vc' ? { engine: 'seed-vc', voice_id: voiceId.value, model_revision_id: modelId.value } : { engine: 'rvc', registered_model_id: rvcModelId.value } })
  } catch { return null }
})
const requestKey = computed(() => request.value ? key(request.value) : '')
const dirty = computed(() => !!baseline.value && requestKey.value !== key(baseline.value.request))
const mixDirty = computed(() => !!baseline.value && key(mix.value) !== key(baseline.value.mix ?? {}))
const reviewed = computed(() => !!request.value && storage.value?.can_start && storageKey.value === requestKey.value)
const renderReady = computed(() => !!store.plan && !draftConflict.value && !alignmentDirty.value && !store.active && !store.disconnected && !busy.value && !dirty.value && mixValid.value && (store.plan.status === 'needs_review' || store.plan.status === 'done') && (store.plan.request.mode === 'convert' || store.plan.alignment_reviewed === true))
const targetAvailable = computed(() => mode.value === 'prepared' || (targetEngine.value === 'rvc' ? rvcModelReady.value : voice.value?.usable === true))
const allowedVoices = computed(() => voices.value.filter(item => item.usable))
const separatorReady = computed(() => store.capabilities?.separation.options.some(item => item.id === (store.plan?.request.separator ?? separator.value) && item.available && item.id !== 'roformer') === true)
watch(() => store.capabilities, available => {
  if (!store.plan && !source.value && !prepared.value) {
    const choices = available?.separation.options ?? [], best = choices.find(item => item.id === 'high' && item.available) ?? choices.find(item => item.id === 'fast' && item.available)
    if (best) separator.value = best.id
  }
})
function describeError(code?: string | null) { const translated = `replacement.errors.${code ?? ''}`, warning = `voiceMix.warnings.${code ?? ''}`; return t(te(translated) ? translated : te(warning) ? warning : 'replacement.errors.generic') }
function size(value?: number | null) { return value == null ? t('replacement.unknown') : `${(value / 1024 ** 2).toFixed(1)} MiB` }
function invalidateReview() { reviewGeneration++; reviewController?.abort(); reviewController = undefined; reviewing.value = false; storage.value = null; storageKey.value = ''; reviewError.value = false }
watch(requestKey, invalidateReview)
function loadRequest(plan: ReplacementPlanResponse) {
  mode.value = plan.request.mode; title.value = plan.request.title; source.value = plan.request.source
  prepared.value = plan.request.mode === 'convert' ? null : plan.request.prepared
  separator.value = plan.request.separator ?? 'high'; fit.value = parseReplacementFitSettings(JSON.parse(JSON.stringify(plan.request.fit ?? {})))
  if (plan.request.mode !== 'prepared') {
    targetEngine.value = plan.request.target.engine
    if (plan.request.target.engine === 'seed-vc') { voiceId.value = plan.request.target.voice_id; modelId.value = plan.request.target.model_revision_id }
    else rvcModelId.value = plan.request.target.registered_model_id
  }
}
function loadMix(plan: ReplacementPlanResponse) { mix.value = parseReplacementMixSettings(JSON.parse(JSON.stringify(plan.mix ?? {}))) }
function loadAlignment(plan: ReplacementPlanResponse, reset: boolean) {
  alignmentInput.value = plan.alignment ?? null; alignmentReviewed.value = [...plan.reviewed_phrase_ids ?? []]
  if (reset) { alignmentEpoch.value++; alignmentDirty.value = false }
}
function useSavedRevision() {
  const plan = store.plan; if (!plan) return
  loadRequest(plan); loadMix(plan); loadAlignment(plan, true); baseline.value = plan; draftConflict.value = false; invalidateReview()
  step.value = plan.status === 'done' ? 4 : plan.status === 'draft' ? 2 : 3
}
function reviewDraftRevision() {
  if (!store.plan || busy.value || store.active) return
  baseline.value = store.plan; draftConflict.value = false; alignmentEditor.value?.reviewRevision(); invalidateReview()
}
watch(() => store.plan, plan => {
  if (!plan) return
  const previous = baseline.value
  if (!previous || previous.id !== plan.id) { useSavedRevision(); return }
  const content = (value: ReplacementPlanResponse) => key([value.request, value.mix, value.alignment, value.reviewed_phrase_ids])
  if ((previous.revision ?? 1) === (plan.revision ?? 1) && content(previous) === content(plan)) return
  const accepted = ownSave?.planId === plan.id && (plan.revision ?? 1) === ownSave.revision + 1
  if (accepted) {
    if (ownSave?.kind === 'request') loadRequest(plan)
    if (ownSave?.kind === 'mix') loadMix(plan)
    if (!alignmentDirty.value || ownSave?.kind === 'alignment') loadAlignment(plan, ownSave?.kind === 'alignment')
    else if (key(previous.alignment) !== key(plan.alignment)) draftConflict.value = true
    baseline.value = plan; return
  }
  if (dirty.value || mixDirty.value || alignmentDirty.value) {
    draftConflict.value = true; invalidateReview()
    if (plan.alignment) { alignmentInput.value = plan.alignment; alignmentReviewed.value = [...plan.reviewed_phrase_ids ?? []] }
    return
  }
  useSavedRevision()
}, { flush: 'sync' })
async function navigate(value: number) { step.value = value; await nextTick(); if (alive) heading.value?.focus() }
function changeVoice() { modelId.value = voice.value?.active_model_id || 'trained' }
function choosePlan(event: Event) { if (event.target instanceof HTMLSelectElement && event.target.value) void store.load(event.target.value) }
async function loadVoices() {
  voiceController?.abort(); const controller = new AbortController(); voiceController = controller; voicesFailed.value = false
  try { const result = await listVoices(controller.signal); if (alive && !controller.signal.aborted) voices.value = result }
  catch { if (alive && !controller.signal.aborted) voicesFailed.value = true }
}
async function review() {
  const current = request.value
  if (!current || busy.value || store.active || draftConflict.value) return
  invalidateReview(); const token = reviewGeneration, controller = new AbortController(), submitted = requestKey.value
  reviewController = controller; reviewing.value = true
  try { const result = await api.previewReplacementStorage(current, controller.signal); if (alive && token === reviewGeneration && submitted === requestKey.value && !controller.signal.aborted) { storage.value = result; storageKey.value = submitted } }
  catch { if (alive && token === reviewGeneration && !controller.signal.aborted) reviewError.value = true }
  finally { if (alive && token === reviewGeneration) { reviewing.value = false; reviewController = undefined } }
}
async function savePlan() {
  const current = request.value
  if (!current || busy.value || !reviewed.value || store.active || draftConflict.value) return
  if (store.plan) await saveOwned('request', () => store.update(current))
  else await store.create(current)
  if (alive && !store.error && store.plan) await navigate(3)
}
function newDraft() {
  if (busy.value || store.active) return
  store.newDraft(); baseline.value = null; draftConflict.value = false; alignmentDirty.value = false; alignmentInput.value = null; source.value = null; prepared.value = null; title.value = ''; step.value = 1; invalidateReview()
}
async function saveOwned(kind: 'request' | 'mix' | 'alignment', work: () => Promise<void> | undefined) {
  if (!store.plan || draftConflict.value || busy.value || store.active) return
  const action = { kind, planId: store.plan.id, revision: store.plan.revision ?? 1 }; ownSave = action
  try { await work(); if (alive && ownSave === action && store.error === 'conflict') draftConflict.value = true } finally { if (ownSave === action) ownSave = null }
}
function saveMix() { return saveOwned('mix', () => store.saveMix(parseReplacementMixSettings(JSON.parse(JSON.stringify(mix.value))))) }
function useDiagnosticPitch(value: PitchRecommendation) {
  if (!diagnosticsReady.value) return
  fit.value = parseReplacementFitSettings({ ...fit.value, pitch_shift_semitones: value.pitch_shift_semitones, pitch_nudge_semitones: value.pitch_nudge_semitones })
  diagnosticNotice.value = 'replacement.diagnostics.pitchDraft'; step.value = 2
}
function useDiagnosticEq(value: EqBand[]) {
  if (!diagnosticsReady.value) return
  mix.value = parseReplacementMixSettings({ ...mix.value, eq_bands: value })
  diagnosticNotice.value = 'replacement.diagnostics.eqDraft'; step.value = 4
}
function saveAlignment(edit: Omit<AlignmentEditRequest, 'expected_revision'>) { return saveOwned('alignment', () => store.saveAlignment(edit)) }
async function render(action: 'preview' | 'render' | 'remix') {
  if (!renderReady.value) return
  try {
    const parsed = parseReplacementMixSettings(mix.value)
    await store.render(action, parsed, action === 'preview' ? { start_sec: previewStart.value, duration_sec: previewLength.value } : undefined)
  } catch { reviewError.value = true }
}
watch(() => store.plan?.id, () => { diagnosticNotice.value = '' })
watch(() => props.planId, id => { if (alive) void store.start(id) })
onMounted(() => { void store.start(props.planId); void loadVoices() })
onBeforeUnmount(() => { alive = false; invalidateReview(); voiceController?.abort(); store.stop() })
</script>
<template>
  <section class="space-y-5" :aria-busy="busy">
    <header class="flex flex-wrap items-start justify-between gap-3"><div><h2 ref="heading" tabindex="-1" class="text-xl font-semibold">{{ t('replacement.title') }}</h2><p class="mt-2 max-w-3xl text-sm text-text-dim">{{ t('replacement.intro') }}</p></div><button type="button" class="replace-button" :disabled="busy || store.active" @click="newDraft">{{ t('replacement.newPlan') }}</button></header>
    <div class="flex flex-wrap items-end gap-3"><label class="replace-label flex-1"><span>{{ t('replacement.savedPlans') }}</span><select :value="store.plan?.id ?? ''" :disabled="busy" class="replace-input" @change="choosePlan"><option value="" disabled>{{ t('replacement.choosePlan') }}</option><option v-for="item in store.plans" :key="item.id" :value="item.id">{{ item.title }} · {{ t(`replacement.statuses.${item.status}`) }}</option></select></label><button type="button" class="replace-button" :disabled="busy" @click="store.refresh">{{ t('replacement.refresh') }}</button></div>
    <div v-if="draftConflict" role="status" class="space-y-2 rounded-lg border border-status-warning p-3 text-sm"><p>{{ t('replacement.draftConflict') }}</p><p>{{ t('replacement.savedRevision', {revision:store.plan?.revision ?? 1,updated:store.plan?.updated_at}) }}</p><details><summary class="min-h-11 cursor-pointer py-2">{{ t('replacement.savedRevisionDetails') }}</summary><pre class="max-h-72 overflow-auto whitespace-pre-wrap break-all text-xs text-text-dim">{{ JSON.stringify({request:store.plan?.request,mix:store.plan?.mix,alignment:store.plan?.alignment,reviewed_phrase_ids:store.plan?.reviewed_phrase_ids},null,2) }}</pre></details><div class="flex flex-wrap gap-3"><button type="button" class="replace-button" :disabled="busy || store.active" @click="useSavedRevision">{{ t('replacement.useSavedRevision') }}</button><button type="button" class="replace-button" :disabled="busy || store.active" @click="reviewDraftRevision">{{ t('replacement.reviewDraftRevision') }}</button></div></div>
    <p v-if="store.disconnected" role="status" class="text-sm text-status-warning">{{ t('replacement.disconnected') }}</p><p v-if="store.error" role="alert" class="text-sm text-status-failed">{{ t(`replacement.${store.error}`) }}</p><p v-if="reviewError" role="alert" class="text-sm text-status-failed">{{ t('replacement.reviewFailed') }}</p>
    <div v-if="store.pendingAction" role="status" class="space-y-2 rounded-lg border border-status-warning p-3 text-sm"><p>{{ t('replacement.pendingAction') }}</p><button type="button" class="replace-button" :disabled="store.busy" @click="store.resumePending">{{ t('replacement.resumeAction') }}</button></div>
    <nav class="grid gap-2 sm:grid-cols-4" :aria-label="t('replacement.stepsLabel')"><button v-for="value in 4" :key="value" type="button" class="replace-button text-left" :class="step === value ? 'border-accent2 bg-accent1/15' : ''" :aria-current="step === value ? 'step' : undefined" @click="navigate(value)">{{ value }}. {{ t(`replacement.steps.${value}`) }}</button></nav>
    <section v-show="step === 1" class="space-y-5 rounded-xl border border-border bg-panel p-4">
      <h3 class="font-semibold">{{ t('replacement.steps.1') }}</h3><div class="grid gap-3 lg:grid-cols-3"><button v-for="value in (['convert','prepared','hybrid'] as const)" :key="value" type="button" class="replace-button space-y-2 text-left" :disabled="busy || store.active" :aria-pressed="mode === value" :class="mode === value ? 'border-accent2' : ''" @click="mode = value"><span class="block font-semibold">{{ t(`replacement.modes.${value}.title`) }}</span><span class="block text-xs text-text-dim">{{ t(`replacement.modes.${value}.detail`) }}</span></button></div>
      <label class="replace-label"><span>{{ t('replacement.name') }}</span><input v-model="title" :disabled="busy || store.active" maxlength="300" class="replace-input"></label>
      <MusicSourcePicker v-model="source" :label="t('replacement.original')" :disabled="store.busy || store.active" @busy="sourceBusy = $event" />
      <p v-if="source" class="break-all text-xs text-text-dim">{{ t('replacement.retainedSource') }} · {{ source.source.kind }} · {{ source.audio_sha256 }}</p><button type="button" class="replace-button" :disabled="!source || !title.trim() || busy" @click="navigate(2)">{{ t('replacement.next') }}</button>
    </section>
    <section v-show="step === 2" class="space-y-5 rounded-xl border border-border bg-panel p-4">
      <h3 class="font-semibold">{{ t('replacement.steps.2') }}</h3>
      <MusicSourcePicker v-if="mode !== 'convert'" v-model="prepared" :label="t('replacement.preparedVocal')" :disabled="store.busy || store.active" @busy="preparedBusy = $event" />
      <p v-if="mode !== 'convert' && prepared" class="break-all text-xs text-text-dim">{{ t('replacement.retainedSource') }} · {{ prepared.audio_sha256 }}</p>
      <fieldset v-if="mode !== 'prepared'" :disabled="busy || store.active" class="space-y-3"><label class="replace-label"><span>{{ t('replacement.engine') }}</span><select v-model="targetEngine" class="replace-input"><option value="seed-vc">Seed-VC</option><option value="rvc">RVC</option></select></label><template v-if="targetEngine === 'seed-vc'"><div class="grid gap-3 sm:grid-cols-2"><label class="replace-label"><span>{{ t('replacement.voice') }}</span><select v-model="voiceId" class="replace-input" @change="changeVoice"><option value="">{{ t('replacement.chooseVoice') }}</option><option v-for="item in allowedVoices" :key="item.id" :value="item.id">{{ item.name }}</option><option v-if="voiceId && !allowedVoices.some(item => item.id === voiceId)" :value="voiceId" disabled>{{ t('replacement.savedVoiceUnavailable') }}</option></select></label><label class="replace-label"><span>{{ t('replacement.model') }}</span><select v-model="modelId" class="replace-input"><option v-if="!selectedModels.length" value="trained">{{ t('replacement.savedModel') }}</option><option v-for="item in selectedModels" :key="item.id" :value="item.id">{{ item.kind }} · {{ item.steps }} · {{ item.id }}</option></select></label></div><p v-if="voicesFailed" role="alert" class="text-sm text-status-warning">{{ t('replacement.voicesFailed') }}</p><p class="text-xs text-text-dim">{{ t('replacement.seedPath') }}</p><p v-if="!store.capabilities?.rvc_inference_ready" class="text-xs text-text-dim">{{ t('replacement.rvcUnavailable') }}</p><button type="button" class="replace-button" @click="loadVoices">{{ t('replacement.refreshVoices') }}</button><RouterLink to="/voice-clone" class="inline-flex min-h-11 items-center text-sm text-accent2 underline">{{ t('replacement.manageVoices') }}</RouterLink></template><RvcModelLibrary v-else v-model="rvcModelId" :voices="voices" :disabled="busy || store.active" @valid="rvcModelReady = $event" /></fieldset>
      <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('replacement.fitSettings') }}</summary><fieldset :disabled="busy || store.active" class="space-y-3"><label class="replace-label"><span>{{ t('replacement.separator') }}</span><select v-model="separator" class="replace-input"><option v-for="choice in store.capabilities?.separation.options ?? []" :key="choice.id" :value="choice.id" :disabled="!choice.available || choice.id === 'roformer'">{{ t(`replacement.separators.${choice.id}`) }} · {{ t(choice.available ? 'replacement.available' : 'replacement.unavailable') }}</option></select></label><RouterLink to="/settings#module-separation" class="inline-flex min-h-11 items-center text-sm text-accent2 underline">{{ t('replacement.setup') }}</RouterLink><div class="grid gap-3 sm:grid-cols-3"><label class="replace-label"><span>{{ t('replacement.maxStretch') }}</span><input v-model.number="fit.max_stretch" type="number" min="1" max="1.5" step="0.01" class="replace-input"></label><label class="replace-label"><span>{{ t('replacement.pitch') }}</span><input v-model.number="fit.pitch_shift_semitones" type="number" min="-12" max="12" step="1" class="replace-input"></label><label class="replace-label"><span>{{ t('replacement.nudge') }}</span><input v-model.number="fit.pitch_nudge_semitones" type="number" min="-1" max="1" step="0.01" class="replace-input"></label></div><label class="replace-label"><span>{{ t('replacement.cleanup') }}</span><select v-model="fit.cleanup" class="replace-input"><option value="off">{{ t('replacement.cleanupOff') }}</option><option value="light" :disabled="!store.capabilities?.cleanup_supported">{{ t('replacement.cleanupLight') }}</option></select></label><label class="flex min-h-11 items-center gap-3 text-sm"><input v-model="fit.keep_breaths" type="checkbox" :disabled="!store.capabilities?.cleanup_supported">{{ t('replacement.keepBreaths') }}</label><p class="text-xs text-text-dim">{{ t('replacement.fitHelp') }} {{ t('replacement.quietHelp') }}</p></fieldset></details>
      <p v-if="!request" class="text-sm text-status-warning">{{ t('replacement.incomplete') }}</p><p v-if="dirty" class="text-sm text-status-warning">{{ t('replacement.dirty') }}</p><button type="button" class="replace-button" :disabled="busy || store.active || draftConflict || !request" @click="review">{{ t('replacement.reviewStorage') }}</button>
      <div v-if="storage" class="space-y-2 rounded-lg border border-border p-3 text-sm"><p>{{ t('replacement.required',{size:size(storage.required_free_bytes)}) }} · {{ t('replacement.free',{size:size(storage.free_bytes)}) }}</p><p v-if="!storage.can_start" class="text-status-warning">{{ t('replacement.diskBlocked') }}</p><button type="button" class="replace-button" :disabled="busy || store.active || draftConflict || !reviewed" @click="savePlan">{{ t(store.plan ? 'replacement.saveChanges' : 'replacement.savePlan') }}</button></div><button v-if="store.plan && !dirty" type="button" class="replace-button" @click="navigate(3)">{{ t('replacement.next') }}</button>
    </section>
    <section v-show="step === 3" class="space-y-4 rounded-xl border border-border bg-panel p-4"><h3 class="font-semibold">{{ t('replacement.steps.3') }}</h3><p class="text-sm text-text-dim">{{ t('replacement.prepareHelp') }}</p><p v-if="store.plan?.alignment" class="text-sm text-text-dim">{{ t('replacement.refreshAnalysisHelp') }}</p><p v-if="!store.plan" class="text-sm text-text-dim">{{ t('replacement.saveFirst') }}</p><template v-else><div class="flex flex-wrap items-center gap-3"><button type="button" class="replace-button" :disabled="busy || store.active || draftConflict || dirty || alignmentDirty || !separatorReady || !targetAvailable" @click="store.prepare">{{ t(store.plan.alignment ? 'replacement.refreshAnalysis' : 'replacement.prepare') }}</button><p v-if="!separatorReady" class="text-xs text-status-warning">{{ t('replacement.errors.replacement_separator_unavailable') }}</p><button v-if="store.active" type="button" class="replace-button" :disabled="store.busy" @click="store.cancel">{{ t('replacement.cancel') }}</button><button v-if="!store.active && (store.plan.status === 'failed' || store.plan.status === 'interrupted' || store.plan.status === 'cancelled')" type="button" class="replace-button" :disabled="busy || draftConflict" @click="store.retry">{{ t('replacement.retry') }}</button></div><p role="status" class="text-sm">{{ t(`replacement.statuses.${store.plan.status ?? 'draft'}`) }} · {{ t(`replacement.phases.${store.plan.phase ?? 'inspecting'}`) }}</p><p class="text-xs text-text-dim">{{ t('replacement.savedRevision',{revision:store.plan.revision ?? 1,updated:store.plan.updated_at}) }}</p><p v-if="store.plan.error_code" role="alert" class="text-sm text-status-failed">{{ describeError(store.plan.error_code) }}</p><p v-if="!store.capabilities?.native_stretch_available" class="text-xs text-status-warning">{{ t('replacement.stretchMissing') }} <RouterLink to="/settings#module-music_dsp" class="underline">{{ t('replacement.setup') }}</RouterLink></p><ReplacementAlignmentEditor v-if="alignmentInput" ref="alignmentEditor" :key="`${store.plan.id}-${alignmentEpoch}`" :alignment="alignmentInput" :reviewed="alignmentReviewed" :disabled="busy || store.active || dirty" :stale="draftConflict || !store.plan.alignment" @dirty="alignmentDirty = $event" @stale="draftConflict ||= $event" @save="saveAlignment" /><p v-if="store.plan.request.mode !== 'convert' && !store.plan.alignment_reviewed" class="text-sm text-status-warning">{{ t('replacement.reviewNeeded') }}</p><button type="button" class="replace-button" @click="navigate(4)">{{ t('replacement.next') }}</button></template></section>
    <section v-show="step === 4" class="space-y-4 rounded-xl border border-border bg-panel p-4"><h3 class="font-semibold">{{ t('replacement.steps.4') }}</h3><ReplacementMixControls v-model="mix" :disabled="busy || store.active" :ir-supported="store.capabilities?.owned_ir_supported === true" @valid="mixValid = $event" /><div class="grid gap-3 sm:grid-cols-2"><label class="replace-label"><span>{{ t('replacement.previewStart') }}</span><input v-model.number="previewStart" type="number" min="0" max="7200" step="0.01" class="replace-input"></label><label class="replace-label"><span>{{ t('replacement.previewLength') }}</span><input v-model.number="previewLength" type="number" min="0.1" max="30" step="0.1" class="replace-input"></label></div><p class="text-xs text-text-dim">{{ t('replacement.remixHelp') }}</p><div class="flex flex-wrap gap-3"><button type="button" class="replace-button" :disabled="!renderReady" @click="render('preview')">{{ t('replacement.preview') }}</button><button type="button" class="replace-button" :disabled="!renderReady" @click="render('render')">{{ t('replacement.render') }}</button><button type="button" class="replace-button" :disabled="!renderReady" @click="render('remix')">{{ t('replacement.remix') }}</button><button type="button" class="replace-button" :disabled="busy || store.active || draftConflict || !store.plan || !mixValid" @click="saveMix">{{ t('replacement.saveMix') }}</button></div></section>
    <p v-if="diagnosticNotice" role="status" class="text-sm text-status-done">{{ t(diagnosticNotice) }}</p>
    <ReplacementDiagnosticsCard v-if="store.plan?.diagnostics" :report="store.plan.diagnostics" :disabled="!diagnosticsReady" @pitch="useDiagnosticPitch" @eq="useDiagnosticEq" />
    <section v-if="store.plan" class="space-y-4"><h3 class="font-semibold">{{ t('replacement.retainedAudio') }}</h3><p class="text-xs text-text-dim">{{ t('replacement.retainedHelp') }}</p><article v-for="artifact in store.plan.artifacts ?? []" :key="artifact.id" class="space-y-3 rounded-lg border border-border p-4"><h4 class="text-sm font-medium">{{ t(`replacement.roles.${artifact.role}`) }}</h4><SpeechAudioPreview v-if="artifact.audio_url" :src="artifact.audio_url" :label="t(`replacement.roles.${artifact.role}`)" preload="none" /><AudioQualityReport v-if="artifact.measurement" :metrics="artifact.measurement" /><a v-if="artifact.audio_url" :href="artifact.audio_url" download class="inline-flex min-h-11 items-center text-sm text-accent2 underline">{{ t('replacement.download') }}</a><details><summary class="min-h-11 cursor-pointer py-2 text-xs">{{ t('replacement.identity') }}</summary><p class="break-all text-xs text-text-dim">{{ artifact.sha256 }} · {{ artifact.duration_sec.toFixed(2) }} s · {{ artifact.sample_rate_hz }} Hz</p><template v-if="artifact.processing"><p v-if="artifact.processing.applied_gain_db != null" class="text-xs text-text-dim">{{ t('replacement.processingGain', { gain: artifact.processing.applied_gain_db.toFixed(2) }) }}</p><p v-if="artifact.processing.cleanup_recipe" class="break-all text-xs text-text-dim">{{ t('replacement.processingCleanup') }}: {{ artifact.processing.cleanup_recipe }} · {{ artifact.processing.low_level_attenuation_db ?? 0 }} dB</p><p v-if="artifact.processing.ir_sha256" class="break-all text-xs text-text-dim">{{ t('replacement.processingIr') }}: {{ artifact.processing.ir_sha256 }} · {{ artifact.processing.wet }} · {{ artifact.processing.ir_gain }}</p><p v-if="artifact.processing.stitch_recipe" class="break-all text-xs text-text-dim">{{ artifact.processing.stitch_recipe }} · {{ artifact.processing.stitched_phrases ?? 0 }}</p><p class="text-xs text-text-dim">{{ t('replacement.processingUnverified') }}</p></template></details></article><p v-for="warning in store.plan.warnings ?? []" :key="warning" class="text-xs text-status-warning">{{ describeError(warning) }}</p></section>
  </section>
</template>
<style scoped>
@reference '../../style.css';
.replace-label { @apply flex min-w-0 flex-col gap-1.5 text-xs text-text-dim; }
.replace-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2; }
.replace-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2 disabled:opacity-50; }
</style>
