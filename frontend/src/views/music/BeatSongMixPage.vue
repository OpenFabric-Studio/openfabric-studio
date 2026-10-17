<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/beatSongMixes'
import { beatSongArtifactUrl } from '../../api/beatSongMixes'
import { listAudioVersions } from '../../api/audioVersions'
import { listBeats, listBeatJobs } from '../../api/beats'
import { ApiError } from '../../api/http'
import { parseBeatSongMixDraft, parseBeatSongMixFailure, parseBeatSongMixStartRequest } from '../../api/generated'
import type { AudioVersion, BeatDocumentSummary, BeatJob, BeatSongLane, BeatSongLaneControl, BeatSongMixDraft, BeatSongMixJob, BeatSongMixPlan, BeatSongMixPreview, BeatSongMixStartRequest, SongSourceInfo, SongSourceRequest, WarpAnchor } from '../../api/generated'
import LibraryPicker from '../../components/editor/LibraryPicker.vue'
import AudioQualityReport from '../../components/shared/AudioQualityReport.vue'
import SpeechAudioPreview from '../voice/SpeechAudioPreview.vue'
import TrackAudioVersions from '../../components/shared/TrackAudioVersions.vue'

const props = defineProps<{ planId?: string }>(), { t } = useI18n()
type Side = 'beat' | 'song'
interface SourceEditor { source: SongSourceRequest['source'] | null; info: SongSourceInfo | null; label: string; versions: AudioVersion[]; versionId: string; start: number; end: number; busy: boolean }
const emptySource = (): SourceEditor => ({ source: null, info: null, label: '', versions: [], versionId: '', start: 0, end: 0, busy: false })
const sources = reactive<Record<Side, SourceEditor>>({ beat: emptySource(), song: emptySource() }), sides: Side[] = ['beat', 'song']
const title = ref(''), mode = ref<NonNullable<BeatSongMixDraft['mode']>>('layer'), direction = ref<NonNullable<BeatSongMixDraft['tempo_strategy']>>('beat_to_song')
const repeats = ref(1), bpm = ref(120), meter = reactive<BeatSongMixDraft['project_meter']>({ numerator: 4, denominator: 4 })
const anchors = ref<WarpAnchor[]>([{ source_sec: 0, target_sec: 0 }, { source_sec: 0, target_sec: 0 }]), timingConfirmed = ref(false)
const bassLeader = ref<NonNullable<BeatSongMixDraft['bass_leader']>>('none'), kickLeader = ref<NonNullable<BeatSongMixDraft['kick_leader']>>('none')
const pitch = ref(0), approximation = ref(false), keyReview = ref<NonNullable<BeatSongMixDraft['key_review']>>('unknown')
const loudness = ref<NonNullable<BeatSongMixDraft['loudness']>>({ profile: 'off', integrated_lufs: -16, true_peak_dbtp: -2 })
const controls = ref<BeatSongLaneControl[]>([]), symbolicOrigin = ref<BeatSongMixDraft['symbolic_origin']>(null)
const plan = ref<BeatSongMixPlan | null>(null), reviewed = ref<BeatSongMixPreview | null>(null), reviewedKey = ref(''), confirmed = ref(false)
const planLookup = ref(''), picker = ref<Side | null>(null), jobs = ref<BeatSongMixJob[]>([]), busy = ref(false), disconnected = ref(false), error = ref('')
const previewStart = ref(0), previewEnd = ref(15), projectIds = ref<Record<string, number>>({}), pending = ref<BeatSongMixStartRequest | null>(null)
const openedTracks = ref<ReadonlySet<string>>(new Set())
function openTrackTools(event: Event, id: string) { if (event.target instanceof HTMLDetailsElement && event.target.open) openedTracks.value = new Set([...openedTracks.value, id]) }
const beats = ref<BeatDocumentSummary[]>([]), beatId = ref(''), renders = ref<BeatJob[]>([]), renderId = ref('')
const storageKey = 'openfabric.beat-song.plan', pendingKey = 'openfabric.beat-song.pending'
let alive = true, operation = 0, operationController: AbortController | undefined, pollController: AbortController | undefined, pollGeneration = 0, timer: ReturnType<typeof setTimeout> | undefined
const sourceControllers: Partial<Record<Side, AbortController>> = {}, sourceGenerations: Record<Side, number> = { beat: 0, song: 0 }
const sourceBusy = computed(() => sides.some(side => sources[side].busy))
const movingDuration = computed(() => direction.value === 'beat_to_song' ? beatDuration.value : songDuration.value)
const targetDuration = computed(() => direction.value === 'beat_to_song' ? songDuration.value : beatDuration.value)
const beatDuration = computed(() => (sources.beat.info?.musical_duration_sec ?? (sources.beat.end - sources.beat.start)) * repeats.value)
const songDuration = computed(() => sources.song.end - sources.song.start)
const draft = computed<BeatSongMixDraft | null>(() => {
  const beat = sources.beat, song = sources.song
  if (!beat.source || !beat.info || !song.source || !song.info) return null
  try {
    const result = parseBeatSongMixDraft({ title: title.value.trim(), beat: { source: beat.source, sha256: beat.info.sha256, excerpt_start_sec: beat.start, excerpt_end_sec: beat.end }, song: { source: song.source, sha256: song.info.sha256, excerpt_start_sec: song.start, excerpt_end_sec: song.end }, mode: mode.value, tempo_strategy: direction.value, beat_repeats: repeats.value, anchors: anchors.value, project_bpm: bpm.value, project_meter: meter, bass_leader: bassLeader.value, kick_leader: kickLeader.value, controls: controls.value, loudness: loudness.value, audio_pitch_semitones: pitch.value, audio_approximation: approximation.value, key_review: keyReview.value, symbolic_origin: symbolicOrigin.value })
    if (sides.some(side => sources[side].end > (sources[side].info?.duration_sec ?? 0) + 1 / (sources[side].info?.sample_rate_hz ?? 48000) || sources[side].end <= sources[side].start || sources[side].end - sources[side].start > 600)) return null
    const first = result.anchors[0], last = result.anchors.at(-1)
    if (first?.source_sec !== 0 || first.target_sec !== 0 || !last || Math.abs(last.source_sec - movingDuration.value) > 1 / 48000 || Math.abs(last.target_sec - targetDuration.value) > 1 / 48000 || targetDuration.value > 600 || movingDuration.value > 600) return null
    for (let index = 1; index < result.anchors.length; index++) {
      const previous = result.anchors[index - 1], current = result.anchors[index]
      if (!previous || !current) return null
      const source = current.source_sec - previous.source_sec, target = current.target_sec - previous.target_sec
      if (source <= 0 || target <= 0 || target / source < .75 - 1e-12 || target / source > 1.5 + 1e-12) return null
    }
    if (beat.info.beat_document && (beat.start !== 0 || beat.end < (beat.info.musical_duration_sec ?? Infinity) - 1 / 48000 || pitch.value !== 0)) return null
    if (!beat.info.beat_document && (repeats.value !== 1 || pitch.value !== 0) && !approximation.value) return null
    return result
  } catch { return null }
})
const draftKey = computed(() => JSON.stringify([title.value, sides.map(side => [sources[side].source, sources[side].info?.sha256, sources[side].start, sources[side].end]), mode.value, direction.value, repeats.value, anchors.value, bpm.value, meter, bassLeader.value, kickLeader.value, controls.value, loudness.value, pitch.value, approximation.value, keyReview.value, symbolicOrigin.value]))
watch(draftKey, () => { reviewed.value = null; confirmed.value = false }, { flush: 'sync' })
watch(() => JSON.stringify([sides.map(side => [sources[side].start, sources[side].end]), direction.value, repeats.value, anchors.value, bpm.value, meter]), () => { timingConfirmed.value = false }, { flush: 'sync' })
watch([previewStart, previewEnd], () => { confirmed.value = false }, { flush: 'sync' })
const ready = computed(() => !!draft.value && timingConfirmed.value && !!reviewed.value && reviewedKey.value === draftKey.value && reviewed.value.storage.can_start && (!reviewed.value.plan.needs_native || reviewed.value.plan.native_ready) && (mode.value !== 'replace_drums' || reviewed.value.plan.separator_ready) && confirmed.value && !busy.value && !sourceBusy.value && !pending.value && !disconnected.value)
const validPreview = computed(() => Number.isFinite(previewStart.value) && Number.isFinite(previewEnd.value) && previewStart.value >= 0 && previewEnd.value > previewStart.value && previewEnd.value - previewStart.value <= 60 && previewEnd.value <= targetDuration.value)
const lanes = computed(() => (plan.value?.lanes ?? []).flatMap(lane => { const control = controls.value.find(item => item.lane_id === lane.id); return control ? [{ ...lane, control }] : [] }))
const drumDetectors = (lane: BeatSongLane) => lanes.value.filter(item => item.role === 'drum' && item.id !== lane.id)
const active = (job: BeatSongMixJob) => ['queued', 'running', 'cancelling'].includes(job.status)
const submissionIdentity = (request: BeatSongMixStartRequest) => JSON.stringify([request.plan_id, request.expected_revision, request.plan_sha256, request.submission_key, request.mode ?? 'full', request.preview_start_sec ?? null, request.preview_end_sec ?? null])
const bytes = (value?: number | null) => value == null ? t('beatSongMixes.unknown') : `${(value / 1024 ** 2).toFixed(1)} MiB`
function fail(cause: unknown): void {
  if (!alive) return
  if (cause instanceof ApiError) {
    try { const parsed = parseBeatSongMixFailure({ code: cause.message }); error.value = t(`beatSongMixes.errors.${parsed.code}`); return } catch { /* Return a bounded app message below. */ }
  }
  error.value = t('beatSongMixes.failed')
}
function remember(key: string, value: string | null): void { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value) } catch { /* Backend ownership is independent of browser storage. */ } }
function begin(): { token: number; controller: AbortController } { operationController?.abort(); operationController = new AbortController(); busy.value = true; error.value = ''; return { token: ++operation, controller: operationController } }
function current(token: number): boolean { return alive && token === operation }
function upsert(job: BeatSongMixJob): void { jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id)].slice(0, 100) }
function applyPlan(value: BeatSongMixPlan, freshReview: boolean): void {
  const priorTimingConfirmation = timingConfirmed.value
  plan.value = value; planLookup.value = value.id; remember(storageKey, value.id)
  const valueDraft = value.draft
  title.value = valueDraft.title; mode.value = valueDraft.mode ?? 'layer'; direction.value = valueDraft.tempo_strategy ?? 'beat_to_song'; repeats.value = valueDraft.beat_repeats ?? 1
  bpm.value = valueDraft.project_bpm; Object.assign(meter, valueDraft.project_meter); anchors.value = valueDraft.anchors.map(anchor => ({ ...anchor }))
  bassLeader.value = valueDraft.bass_leader ?? 'none'; kickLeader.value = valueDraft.kick_leader ?? 'none'; pitch.value = valueDraft.audio_pitch_semitones ?? 0; approximation.value = valueDraft.audio_approximation ?? false; keyReview.value = valueDraft.key_review ?? 'unknown'; symbolicOrigin.value = valueDraft.symbolic_origin ?? null
  loudness.value = { profile: 'off', integrated_lufs: -16, true_peak_dbtp: -2, ...valueDraft.loudness }
  for (const side of sides) { stopSource(side); const info = value[side], input = valueDraft[side]; Object.assign(sources[side], emptySource(), { source: info.source, info, label: t(`beatSongMixes.${side}`), versionId: info.version_id, start: input.excerpt_start_sec ?? 0, end: input.excerpt_end_sec }) }
  controls.value = value.lanes.map(({ control }) => ({ ...control, eq_bands: control.eq_bands?.map(band => ({ ...band })) ?? [], duck: control.duck ? { ...control.duck } : null }))
  previewEnd.value = Math.min(60, value.target_duration_sec); previewStart.value = 0; confirmed.value = false
  timingConfirmed.value = freshReview && priorTimingConfirmation
  if (!freshReview) { reviewed.value = null; timingConfirmed.value = false }
}
async function openPlan(value = planLookup.value): Promise<void> {
  const { token, controller } = begin()
  try { const result = await api.getBeatSongPlan(value.trim(), controller.signal); if (current(token)) applyPlan(result, false) } catch (cause) { if (current(token)) fail(cause) } finally { if (current(token)) busy.value = false }
}
function stopSource(side: Side): void { sourceControllers[side]?.abort(); sourceGenerations[side]++; sources[side].busy = false }
function changedSource(side: Side): void { stopSource(side); sources[side].info = null; controls.value = []; symbolicOrigin.value = null; timingConfirmed.value = false }
function changeVersion(side: Side): void {
  changedSource(side); const editor = sources[side], selected = editor.versions.find(item => item.id === editor.versionId)
  editor.source = selected ? { kind: 'version', track_id: selected.track_id, version_id: selected.id } : null
}
async function pickTrack(payload: { trackId?: number; sourceLabel: string }): Promise<void> {
  const side = picker.value; picker.value = null; if (!side || !payload.trackId) return
  changedSource(side); Object.assign(sources[side], emptySource(), { label: payload.sourceLabel, busy: true })
  const token = sourceGenerations[side], controller = new AbortController(); sourceControllers[side] = controller
  try {
    const result = await listAudioVersions(payload.trackId, controller.signal)
    if (!alive || token !== sourceGenerations[side]) return
    const versions = (result.versions ?? []).filter(item => item.status === 'done')
    sources[side].versions = versions; sources[side].versionId = versions[0]?.id ?? ''; sources[side].busy = false; changeVersion(side)
  } catch (cause) { if (alive && token === sourceGenerations[side]) fail(cause) } finally { if (alive && token === sourceGenerations[side]) sources[side].busy = false }
}
async function inspect(side: Side): Promise<void> {
  const source = sources[side].source; if (!source) return
  stopSource(side); sources[side].info = null; sources[side].busy = true; controls.value = []; timingConfirmed.value = false
  const token = sourceGenerations[side], controller = new AbortController(); sourceControllers[side] = controller
  try {
    const info = await api.inspectBeatSongSource({ source }, controller.signal)
    if (!alive || token !== sourceGenerations[side]) return
    sources[side].info = info; sources[side].start = 0; sources[side].end = Math.min(600, info.musical_duration_sec ?? info.duration_sec)
  } catch (cause) { if (alive && token === sourceGenerations[side]) fail(cause) } finally { if (alive && token === sourceGenerations[side]) sources[side].busy = false }
}
async function loadBeats(): Promise<void> {
  const { token, controller } = begin(); try { const result = await listBeats(controller.signal); if (current(token)) beats.value = result.documents } catch (cause) { if (current(token)) fail(cause) } finally { if (current(token)) busy.value = false }
}
function changeBeat(): void { renders.value = []; renderId.value = '' }
async function loadRenders(): Promise<void> {
  const selected = beatId.value, { token, controller } = begin()
  try { const result = await listBeatJobs(selected, controller.signal); if (current(token) && selected === beatId.value) renders.value = result.jobs.filter(job => job.status === 'done' && job.request.mode === 'full' && job.manifest?.track_id) } catch (cause) { if (current(token)) fail(cause) } finally { if (current(token)) busy.value = false }
}
function chooseRender(): void {
  const job = renders.value.find(item => item.id === renderId.value), artifact = job?.manifest?.artifacts.find(item => item.kind === 'mix'); if (!job || !artifact) return
  changedSource('beat'); Object.assign(sources.beat, emptySource(), { source: { kind: 'beat_render', beat_id: job.beat_id, beat_revision: job.beat_revision, render_id: job.id, sha256: artifact.sha256 }, label: beats.value.find(item => item.id === job.beat_id)?.title ?? job.beat_id })
}
function spanMap(): void { if (movingDuration.value > 0 && targetDuration.value > 0) anchors.value = [{ source_sec: 0, target_sec: 0 }, { source_sec: movingDuration.value, target_sec: targetDuration.value }]; timingConfirmed.value = false }
function addAnchor(): void { const previous = anchors.value.at(-2), last = anchors.value.at(-1); if (previous && last && anchors.value.length < 4096) anchors.value.splice(-1, 0, { source_sec: (previous.source_sec + last.source_sec) / 2, target_sec: (previous.target_sec + last.target_sec) / 2 }); timingConfirmed.value = false }
function highpass(control: BeatSongLaneControl, event: Event): void { if (event.target instanceof HTMLInputElement) control.highpass_hz = event.target.checked ? 80 : null }
function duck(control: BeatSongLaneControl, event: Event): void { if (event.target instanceof HTMLSelectElement) control.duck = event.target.value ? { sidechain_input_id: event.target.value, threshold_dbfs: -24, ratio: 4, knee_db: 6, attack_ms: 10, release_ms: 250, max_reduction_db: 6 } : null }
async function review(): Promise<void> {
  const request = draft.value; if (!request || !timingConfirmed.value || pending.value) return
  const key = draftKey.value, { token, controller } = begin(); reviewed.value = null
  try {
    const result = plan.value ? await api.updateBeatSongPlan(plan.value.id, { expected_revision: plan.value.revision, draft: request }, controller.signal) : await api.createBeatSongPlan(request, controller.signal)
    if (!current(token)) return
    if (key !== draftKey.value) { plan.value = result.plan; planLookup.value = result.plan.id; remember(storageKey, result.plan.id); return }
    applyPlan(result.plan, true); reviewedKey.value = draftKey.value; reviewed.value = result; disconnected.value = false
  } catch (cause) { if (current(token)) fail(cause) } finally { if (current(token)) busy.value = false }
}
async function submit(request: BeatSongMixStartRequest): Promise<void> {
  pending.value = request; remember(pendingKey, JSON.stringify(request)); const { token, controller } = begin(); suspendPolling()
  try { const job = await api.startBeatSongMix(request, controller.signal); if (current(token)) { upsert(job); pending.value = null; remember(pendingKey, null); confirmed.value = false; reviewed.value = null } } catch (cause) { if (current(token)) fail(cause) } finally { if (current(token)) busy.value = false; schedulePoll() }
}
async function render(kind: 'preview' | 'full'): Promise<void> {
  if (!ready.value || !plan.value || kind === 'preview' && !validPreview.value) return
  await submit(parseBeatSongMixStartRequest({ plan_id: plan.value.id, expected_revision: plan.value.revision, plan_sha256: plan.value.sha256, submission_key: crypto.randomUUID(), mode: kind, ...(kind === 'preview' ? { preview_start_sec: previewStart.value, preview_end_sec: previewEnd.value } : {}) }))
}
async function jobAction(job: BeatSongMixJob, name: 'cancel' | 'retry-save' | 'project'): Promise<void> {
  const { token, controller } = begin(); suspendPolling()
  try {
    if (name === 'project') { const value = await api.publishBeatSongProject(job, controller.signal); if (current(token)) projectIds.value[job.id] = value.project_id }
    else { const value = await (name === 'cancel' ? api.cancelBeatSongMix(job.id, controller.signal) : api.retryBeatSongSave(job.id, controller.signal)); if (current(token)) upsert(value) }
  } catch (cause) { if (current(token)) fail(cause) } finally { if (current(token)) busy.value = false; schedulePoll() }
}
function suspendPolling(): void { pollGeneration++; pollController?.abort(); if (timer) clearTimeout(timer) }
function schedulePoll(): void { if (timer) clearTimeout(timer); if (alive && !disconnected.value && (jobs.value.some(active) || pending.value)) timer = setTimeout(() => { if (busy.value) schedulePoll(); else void refreshJobs() }, 1500) }
async function refreshJobs(): Promise<void> {
  pollController?.abort(); const controller = new AbortController(); pollController = controller; const token = ++pollGeneration
  try {
    const result = await api.listBeatSongJobs(controller.signal); if (!alive || token !== pollGeneration) return
    jobs.value = result.jobs; disconnected.value = false
    const awaiting = pending.value
    if (awaiting && result.jobs.some(job => submissionIdentity(job.request) === submissionIdentity(awaiting))) { pending.value = null; remember(pendingKey, null) }
    schedulePoll()
  } catch { if (alive && token === pollGeneration) { disconnected.value = true; reviewed.value = null; confirmed.value = false } }
}
function reset(): void {
  operationController?.abort(); operation++; busy.value = false; for (const side of sides) { stopSource(side); Object.assign(sources[side], emptySource()) }
  plan.value = null; reviewed.value = null; controls.value = []; title.value = ''; planLookup.value = ''; timingConfirmed.value = false; symbolicOrigin.value = null; remember(storageKey, null)
  mode.value = 'layer'; direction.value = 'beat_to_song'; repeats.value = 1; bpm.value = 120; Object.assign(meter, { numerator: 4, denominator: 4 }); anchors.value = [{ source_sec: 0, target_sec: 0 }, { source_sec: 0, target_sec: 0 }]
  bassLeader.value = 'none'; kickLeader.value = 'none'; pitch.value = 0; approximation.value = false; keyReview.value = 'unknown'; loudness.value = { profile: 'off', integrated_lufs: -16, true_peak_dbtp: -2 }; error.value = ''
}
watch(() => props.planId, value => { if (value) { planLookup.value = value; void openPlan(value) } })
onMounted(() => {
  let saved: string | null = null
  try { saved = localStorage.getItem(storageKey); const value = localStorage.getItem(pendingKey); if (value) pending.value = parseBeatSongMixStartRequest(JSON.parse(value)) } catch { remember(pendingKey, null) }
  const selected = props.planId ?? saved; if (selected && selected.length === 32 && /^[a-f0-9]{32}$/.test(selected)) { planLookup.value = selected; void openPlan(selected) }
  void refreshJobs()
})
onBeforeUnmount(() => { alive = false; operation++; pollGeneration++; operationController?.abort(); pollController?.abort(); for (const side of sides) stopSource(side); if (timer) clearTimeout(timer) })
</script>

<template>
  <section aria-labelledby="beat-song-mix-heading" class="mx-auto max-w-5xl space-y-5 p-4 text-text">
    <header class="space-y-2"><h2 id="beat-song-mix-heading" class="text-xl font-semibold">{{ t('beatSongMixes.title') }}</h2><p class="text-sm text-text-dim">{{ t('beatSongMixes.intro') }}</p></header>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
    <p v-if="disconnected" role="status" class="text-sm text-amber-300">{{ t('beatSongMixes.disconnected') }}</p>
    <div v-if="pending" class="space-y-2 rounded-lg border border-amber-500/40 p-3"><p>{{ t('beatSongMixes.uncertain') }}</p><button type="button" :disabled="busy" @click="submit(pending)">{{ t('beatSongMixes.recover') }}</button></div>
    <div class="flex flex-wrap items-end gap-3"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.saved') }}<input v-model="planLookup" data-plan-id maxlength="32" class="w-72 rounded border border-border bg-panel p-2" /></label><button type="button" @click="openPlan()">{{ t('beatSongMixes.open') }}</button><button type="button" :disabled="busy" @click="refreshJobs">{{ t('beatSongMixes.reconnect') }}</button><button type="button" :disabled="busy || !!pending" @click="reset">{{ t('beatSongMixes.new') }}</button></div>
    <label class="grid gap-1 text-sm">{{ t('beatSongMixes.name') }}<input v-model="title" data-title maxlength="120" class="rounded border border-border bg-panel p-2" :disabled="busy" /></label>
    <p class="text-xs text-text-dim">{{ t('beatSongMixes.sourceHelp') }}</p>
    <div class="grid gap-4 md:grid-cols-2">
      <section v-for="side in sides" :key="side" :data-source-beat="side === 'beat' ? '' : undefined" class="space-y-3 rounded-lg border border-border bg-panel p-4">
        <h2 class="font-medium">{{ t(`beatSongMixes.${side}`) }}</h2><button type="button" :disabled="busy" @click="picker = side">{{ t(side === 'beat' ? 'beatSongMixes.chooseBeat' : 'beatSongMixes.chooseSong') }}</button>
        <p v-if="sources[side].label" class="text-xs">{{ sources[side].label }}</p>
        <label v-if="sources[side].versions.length" class="grid gap-1 text-xs">{{ t('beatSongMixes.version') }}<select v-model="sources[side].versionId" :disabled="busy || sources[side].busy" class="rounded border border-border bg-panel-2 p-2" @change="changeVersion(side)"><option v-for="version in sources[side].versions" :key="version.id" :value="version.id">{{ version.voice_name || version.kind }} · {{ version.id }}</option></select></label>
        <p v-else-if="sources[side].info" class="break-all text-xs text-text-dim">{{ t('beatSongMixes.pinned', { track: sources[side].info?.track_id, version: sources[side].info?.version_id }) }}</p>
        <button type="button" :disabled="busy || sources[side].busy || !sources[side].source" @click="inspect(side)">{{ t(side === 'beat' ? 'beatSongMixes.inspectBeat' : 'beatSongMixes.inspectSong') }}</button><p v-if="sources[side].busy" role="status" class="text-xs">{{ t('beatSongMixes.inspecting') }}</p>
        <template v-if="sources[side].info"><p class="text-xs">{{ t('beatSongMixes.header', { seconds: sources[side].info?.duration_sec.toFixed(3), rate: sources[side].info?.sample_rate_hz, channels: sources[side].info?.channels }) }}</p><p v-if="sources[side].info?.musical_duration_sec" class="text-xs">{{ t('beatSongMixes.musical', { seconds: sources[side].info?.musical_duration_sec }) }}</p><p class="break-all text-xs text-text-dim">{{ t('beatSongMixes.hash') }}: {{ sources[side].info?.sha256 }}</p></template>
        <div class="grid grid-cols-2 gap-2"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.start') }}<input v-model.number="sources[side].start" type="number" min="0" max="21600" step="0.001" :disabled="busy || !!sources[side].info?.beat_document" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.end') }}<input v-model.number="sources[side].end" type="number" min="0" max="21600" step="0.001" :disabled="busy" /></label></div>
      </section>
    </div>
    <LibraryPicker v-if="picker" track-only :full-mix-label="t('beatSongMixes.fullMix')" @pick="pickTrack" @close="picker = null" />
    <details class="rounded-lg border border-border p-3"><summary>{{ t('beatSongMixes.authored') }}</summary><div class="mt-3 flex flex-wrap items-end gap-3"><button type="button" :disabled="busy" @click="loadBeats">{{ t('beatSongMixes.loadBeats') }}</button><label class="grid gap-1 text-xs">{{ t('beatSongMixes.beatDocument') }}<select v-model="beatId" :disabled="busy" @change="changeBeat"><option value="">{{ t('beatSongMixes.none') }}</option><option v-for="beat in beats" :key="beat.id" :value="beat.id">{{ beat.title }}</option></select></label><button type="button" :disabled="busy || !beatId" @click="loadRenders">{{ t('beatSongMixes.loadRenders') }}</button><label class="grid gap-1 text-xs">{{ t('beatSongMixes.beatRender') }}<select v-model="renderId" :disabled="busy"><option value="">{{ t('beatSongMixes.none') }}</option><option v-for="renderJob in renders" :key="renderJob.id" :value="renderJob.id">{{ renderJob.id }} · r{{ renderJob.beat_revision }}</option></select></label><button type="button" :disabled="busy || !renderId" @click="chooseRender">{{ t('beatSongMixes.selectRender') }}</button></div><p class="mt-2 text-xs text-text-dim">{{ t('beatSongMixes.authoredHelp') }}</p></details>
    <fieldset :disabled="busy" class="space-y-4 rounded-lg border border-border bg-panel p-4">
      <legend class="px-1 font-medium">{{ t('beatSongMixes.assembly') }}</legend>
      <div class="grid gap-3 sm:grid-cols-3"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.assembly') }}<select v-model="mode" @change="controls = []"><option value="layer">{{ t('beatSongMixes.layer') }}</option><option value="replace_drums">{{ t('beatSongMixes.replace_drums') }}</option></select></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.direction') }}<select v-model="direction"><option value="beat_to_song">{{ t('beatSongMixes.beat_to_song') }}</option><option value="song_to_beat">{{ t('beatSongMixes.song_to_beat') }}</option></select></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.repeats') }}<input v-model.number="repeats" type="number" min="1" max="128" step="1" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.bpm') }}<input v-model.number="bpm" data-bpm type="number" min="20" max="300" step="0.01" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.numerator') }}<input v-model.number="meter.numerator" type="number" min="1" max="32" step="1" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.denominator') }}<select v-model="meter.denominator"><option v-for="unit in [2,4,8,16]" :key="unit" :value="unit">{{ unit }}</option></select></label></div>
      <h2 class="text-sm font-medium">{{ t('beatSongMixes.anchors') }}</h2><p class="text-xs text-text-dim">{{ t('beatSongMixes.anchorHelp') }}</p>
      <div v-for="(anchor,index) in anchors" :key="index" class="flex items-end gap-2"><label class="grid flex-1 gap-1 text-xs">{{ t('beatSongMixes.sourceSeconds') }}<input v-model.number="anchor.source_sec" type="number" min="0" max="600" step="0.001" :disabled="index === 0" /></label><label class="grid flex-1 gap-1 text-xs">{{ t('beatSongMixes.targetSeconds') }}<input v-model.number="anchor.target_sec" type="number" min="0" max="600" step="0.001" :disabled="index === 0" /></label><button v-if="index > 0 && index < anchors.length-1" type="button" @click="anchors.splice(index,1)">{{ t('beatSongMixes.remove') }}</button></div>
      <div class="flex flex-wrap gap-3"><button type="button" :disabled="movingDuration <= 0 || targetDuration <= 0" @click="spanMap">{{ t('beatSongMixes.spanMap') }}</button><button type="button" :disabled="anchors.length >= 4096" @click="addAnchor">{{ t('beatSongMixes.addAnchor') }}</button></div>
      <label class="flex gap-2 text-sm"><input v-model="timingConfirmed" data-timing-confirm type="checkbox" />{{ t('beatSongMixes.timingConfirm') }}</label>
      <div class="grid gap-3 sm:grid-cols-2"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.key') }}<select v-model="keyReview"><option value="unknown">{{ t('beatSongMixes.keyUnknown') }}</option><option value="operator_reviewed">{{ t('beatSongMixes.keyReviewed') }}</option></select></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.pitch') }}<input v-model.number="pitch" type="number" min="-12" max="12" step="0.01" :disabled="!!sources.beat.info?.beat_document" /></label></div><p class="text-xs text-text-dim">{{ t('beatSongMixes.symbolicHelp') }}</p><label class="flex gap-2 text-xs"><input v-model="approximation" type="checkbox" />{{ t('beatSongMixes.approximation') }}</label>
      <h2 class="text-sm font-medium">{{ t('beatSongMixes.leaders') }}</h2><p class="text-xs text-text-dim">{{ t('beatSongMixes.leaderHelp') }}</p><div class="grid grid-cols-2 gap-3"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.bassLeader') }}<select v-model="bassLeader"><option v-for="side in ['none','beat','song']" :key="side" :value="side">{{ t(`beatSongMixes.${side}`) }}</option></select></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.kickLeader') }}<select v-model="kickLeader"><option v-for="side in ['none','beat','song']" :key="side" :value="side">{{ t(`beatSongMixes.${side}`) }}</option></select></label></div>
      <div class="grid gap-3 sm:grid-cols-3"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.loudness') }}<select v-model="loudness.profile"><option v-for="profile in ['off','music','spoken_word','ebu','custom']" :key="profile" :value="profile">{{ t(`beatSongMixes.${profile}`) }}</option></select></label><template v-if="loudness.profile === 'custom'"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.lufs') }}<input v-model.number="loudness.integrated_lufs" type="number" min="-40" max="-8" step="0.1" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.peak') }}<input v-model.number="loudness.true_peak_dbtp" type="number" min="-9" max="-0.1" step="0.1" /></label></template></div>
    </fieldset>
    <section class="space-y-3 rounded-lg border border-border p-4"><h2 class="font-medium">{{ t('beatSongMixes.lanes') }}</h2><p class="text-xs text-text-dim">{{ t('beatSongMixes.laneHelp') }}</p>
      <fieldset v-for="lane in lanes" :key="lane.id" :disabled="busy" class="space-y-3 rounded border border-border bg-panel p-3"><legend class="px-1 text-sm">{{ lane.label }} · {{ lane.source }} / {{ lane.role }}</legend>
        <div class="grid gap-3 sm:grid-cols-4"><label class="flex items-center gap-2 text-xs"><input v-model="lane.control.muted" type="checkbox" :disabled="mode === 'replace_drums' && lane.source === 'song' && lane.role === 'drum'" />{{ t('beatSongMixes.muted') }}</label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.gain') }}<input v-model.number="lane.control.gain_db" type="number" min="-12.0412" max="12.0412" step="0.1" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.pan') }}<input v-model.number="lane.control.pan" type="number" min="-1" max="1" step="0.05" /></label><div><label class="flex gap-2 text-xs"><input type="checkbox" :checked="lane.control.highpass_hz != null" @change="highpass(lane.control,$event)" />{{ t('beatSongMixes.highpass') }}</label><label v-if="lane.control.highpass_hz != null" class="grid gap-1 text-xs">{{ t('beatSongMixes.highpassHz') }}<input v-model.number="lane.control.highpass_hz" type="number" min="20" max="500" step="1" /></label></div></div>
        <details><summary class="text-xs">{{ t('beatSongMixes.eq') }}</summary><div v-for="(band,index) in lane.control.eq_bands" :key="index" class="mt-2 grid grid-cols-4 gap-2"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.frequency') }}<input v-model.number="band.frequency_hz" type="number" min="20" max="20000" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.q') }}<input v-model.number="band.q" type="number" min="0.1" max="12" step="0.1" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.eqGain') }}<input v-model.number="band.gain_db" type="number" min="-6" max="6" step="0.1" /></label><button type="button" @click="lane.control.eq_bands?.splice(index,1)">{{ t('beatSongMixes.remove') }}</button></div><button type="button" :disabled="(lane.control.eq_bands?.length ?? 0) >= 10" @click="(lane.control.eq_bands ??= []).push({frequency_hz:1000,q:1,gain_db:0})">{{ t('beatSongMixes.addEq') }}</button></details>
        <label class="grid gap-1 text-xs">{{ t('beatSongMixes.duck') }}<select :value="lane.control.duck?.sidechain_input_id ?? ''" @change="duck(lane.control,$event)"><option value="">{{ t('beatSongMixes.noDuck') }}</option><option v-for="detector in drumDetectors(lane)" :key="detector.id" :value="detector.id">{{ detector.label }}</option></select></label><p class="text-xs text-text-dim">{{ t('beatSongMixes.duckHelp') }}</p>
        <div v-if="lane.control.duck" class="grid grid-cols-2 gap-2 sm:grid-cols-4"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.threshold') }}<input v-model.number="lane.control.duck.threshold_dbfs" type="number" min="-60" max="0" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.ratio') }}<input v-model.number="lane.control.duck.ratio" type="number" min="1" max="8" step="0.1" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.knee') }}<input v-model.number="lane.control.duck.knee_db" type="number" min="0" max="12" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.attack') }}<input v-model.number="lane.control.duck.attack_ms" type="number" min="1" max="100" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.release') }}<input v-model.number="lane.control.duck.release_ms" type="number" min="20" max="2000" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.reduction') }}<input v-model.number="lane.control.duck.max_reduction_db" type="number" min="0" max="12" /></label></div>
      </fieldset>
    </section>
    <section class="space-y-3 rounded-lg border border-border bg-panel p-4">
      <button type="button" :disabled="busy || sourceBusy || !draft || !timingConfirmed || !!pending" @click="review">{{ t('beatSongMixes.review') }}</button>
      <p v-if="plan" class="break-all text-xs text-text-dim">{{ t('beatSongMixes.revision',{id:plan.id,revision:plan.revision}) }}</p>
      <template v-if="reviewed"><p class="text-xs">{{ t('beatSongMixes.ratios', { minimum: reviewed.plan.minimum_ratio.toFixed(3), maximum: reviewed.plan.maximum_ratio.toFixed(3) }) }}</p><p v-for="warning in reviewed.plan.warnings" :key="warning" class="text-xs text-amber-300">{{ t(`beatSongMixes.warnings.${warning}`) }}</p><p v-if="reviewed.plan.needs_native && !reviewed.plan.native_ready" role="status" class="text-xs text-amber-300">{{ t('beatSongMixes.nativeMissing') }}</p><p v-if="mode === 'replace_drums' && !reviewed.plan.separator_ready" role="status" class="text-xs text-amber-300">{{ t('beatSongMixes.separatorMissing') }}</p><p class="text-xs">{{ t('beatSongMixes.storage',{retained:bytes(reviewed.storage.retained_bytes),temporary:bytes(reviewed.storage.temporary_bytes),required:bytes(reviewed.storage.required_free_bytes),free:bytes(reviewed.storage.free_bytes)}) }}</p><p v-if="!reviewed.storage.can_start" role="status" class="text-xs text-amber-300">{{ t('beatSongMixes.noSpace') }}</p><label class="flex gap-2 text-sm"><input v-model="confirmed" data-storage-confirm type="checkbox" :disabled="busy || !reviewed.storage.can_start" />{{ t('beatSongMixes.storageConfirm') }}</label></template>
      <div class="grid gap-3 sm:grid-cols-2"><label class="grid gap-1 text-xs">{{ t('beatSongMixes.previewStart') }}<input v-model.number="previewStart" type="number" min="0" max="600" step="0.01" :disabled="busy" /></label><label class="grid gap-1 text-xs">{{ t('beatSongMixes.previewEnd') }}<input v-model.number="previewEnd" type="number" min="0" max="600" step="0.01" :disabled="busy" /></label></div><p class="text-xs text-text-dim">{{ t('beatSongMixes.previewHelp') }}</p><div class="flex flex-wrap gap-3"><button type="button" :disabled="!ready || !validPreview" @click="render('preview')">{{ t('beatSongMixes.preview') }}</button><button type="button" :disabled="!ready" @click="render('full')">{{ t('beatSongMixes.render') }}</button></div>
    </section>
    <section class="space-y-3"><h2 class="font-medium">{{ t('beatSongMixes.history') }}</h2><p v-if="!jobs.length" class="text-xs text-text-dim">{{ t('beatSongMixes.empty') }}</p>
      <article v-for="job in jobs" :key="job.id" class="space-y-3 rounded-lg border border-border bg-panel p-4"><h3 class="text-sm font-medium">{{ job.plan.draft.title }} · {{ t(`beatSongMixes.statuses.${job.status}`) }}</h3><p class="text-xs text-text-dim">{{ job.request.mode }} · {{ t(`beatSongMixes.phases.${job.progress.phase}`) }} <span v-if="job.progress.total">{{ job.progress.completed ?? 0 }}/{{ job.progress.total }}</span></p><p v-if="job.error" role="status" class="text-xs text-amber-300">{{ t(`beatSongMixes.errors.${job.error.code}`) }}</p>
        <div class="flex flex-wrap gap-3 text-xs"><button type="button" @click="openPlan(job.plan.id)">{{ t('beatSongMixes.loadJobPlan') }}</button><button v-if="active(job)" type="button" :disabled="busy || job.status === 'cancelling'" @click="jobAction(job,'cancel')">{{ t('beatSongMixes.cancel') }}</button><button v-if="job.status === 'saving' || job.error?.code === 'song_save_failed'" type="button" :disabled="busy" @click="jobAction(job,'retry-save')">{{ t('beatSongMixes.retrySave') }}</button></div>
        <template v-if="job.manifest"><p v-if="job.manifest.track_id" class="text-xs">{{ t('beatSongMixes.track',{id:job.manifest.track_id}) }}</p><details v-if="job.status === 'done' && job.manifest.track_id" @toggle="openTrackTools($event, job.id)"><summary class="min-h-11 cursor-pointer py-3 text-sm text-accent2">{{ t('beatSongMixes.trackTools') }}</summary><TrackAudioVersions v-if="openedTracks.has(job.id)" :track-id="job.manifest.track_id" /></details><SpeechAudioPreview v-for="artifact in job.manifest.artifacts.filter(item=>item.kind==='mix')" :key="artifact.id" :src="beatSongArtifactUrl(job.id,artifact.id)" :label="job.plan.draft.title" preload="none" /><AudioQualityReport :metrics="job.manifest.metrics" /><p v-for="warning in job.manifest.warnings" :key="warning" class="text-xs text-amber-300">{{ t(`beatSongMixes.warnings.${warning}`) }}</p>
          <div class="flex flex-wrap gap-3 text-xs"><a v-for="artifact in job.manifest.artifacts.filter(item=>item.kind==='mix'||item.kind==='bundle')" :key="artifact.id" :href="beatSongArtifactUrl(job.id,artifact.id)" download>{{ t('beatSongMixes.download',{filename:artifact.filename}) }}</a><RouterLink v-if="projectIds[job.id] || job.manifest.project_id" :to="`/editor/${projectIds[job.id] || job.manifest.project_id}`">{{ t('beatSongMixes.openEditor') }}</RouterLink><button v-else-if="job.status === 'done' && job.request.mode === 'full'" type="button" :disabled="busy" @click="jobAction(job,'project')">{{ t('beatSongMixes.editor') }}</button></div>
          <details><summary class="text-xs">{{ t('beatSongMixes.recipe') }}</summary><div class="mt-2 space-y-2 break-all text-xs text-text-dim"><p>{{ job.id }} · {{ job.plan.id }} · r{{ job.plan.revision }} · {{ job.plan.sha256 }}</p><p v-for="source in job.manifest.sources" :key="source.source_id">{{ source.source.kind }} · #{{ source.track_id }} · {{ source.version_id }} · {{ source.sha256 }}</p><a v-for="artifact in job.manifest.artifacts.filter(item=>item.kind!=='mix'&&item.kind!=='bundle')" :key="artifact.id" class="block" :href="beatSongArtifactUrl(job.id,artifact.id)" download>{{ t('beatSongMixes.download',{filename:artifact.filename}) }}</a></div></details>
        </template>
      </article>
    </section>
  </section>
</template>

<style scoped>
button { cursor: pointer; border: 1px solid var(--color-border); border-radius: .4rem; padding: .4rem .7rem; }
button:disabled { cursor: default; opacity: .45; }
input[type="number"], select { width: 100%; min-width: 0; border: 1px solid var(--color-border); border-radius: .3rem; background: var(--color-panel-2); padding: .4rem; }
a { color: var(--color-accent); text-decoration: underline; }
</style>
