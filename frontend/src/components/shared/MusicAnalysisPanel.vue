<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as api from '../../api/musicAnalysis'
import { ApiError } from '../../api/http'
import type { KeyEstimate, MusicAnalysisJob, MusicAnalysisOverrides, MusicAnalysisRequest, MusicAnalysisStoragePreview } from '../../api/generated'
import { createPollingLoop, type PollContext } from '../../composables/polling'
import AudioQualityReport from './AudioQualityReport.vue'

const props = defineProps<{ source: api.MusicSource }>()
const { t, te } = useI18n()
const panelId = useId(), expanded = ref(false), busy = ref(false), loaded = ref(false), error = ref(''), notice = ref('')
const job = shallowRef<MusicAnalysisJob | null>(null), now = ref(Date.now())
const forecast = shallowRef<MusicAnalysisStoragePreview | null>(null), forecastFresh = ref(false)
const profile = ref<NonNullable<MusicAnalysisRequest['profile']>>('music-v1')
const analysisRequest = computed<MusicAnalysisRequest>(() => profile.value === 'music-v1' ? { source: props.source } : { source: props.source, profile: profile.value })
const tempo = ref(''), meter = ref(''), key = ref(''), revision = ref(0)
const sourceKey = computed(() => api.musicSourceKey(props.source))
const result = computed(() => job.value?.result ?? null)
const estimatedBeats = computed(() => result.value?.engines?.some(engine => engine.task === 'beats' && engine.name === 'beat-this-small0') === true)
const active = computed(() => !!job.value && (!job.value.status || job.value.status === 'queued' || job.value.status === 'running'))
const canStart = computed(() => loaded.value && forecastFresh.value && forecast.value?.can_start)
const noteNames = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']
const modes: Array<NonNullable<KeyEstimate['mode']>> = ['major', 'minor']
const keyOptions = noteNames.flatMap((note, tonic) => modes.map(mode => ({ value: `${tonic}:${mode}`, tonic, mode, note })))
const valid = computed(() => (!tempo.value || Number.isFinite(Number(tempo.value)) && Number(tempo.value) >= 20 && Number(tempo.value) <= 400)
  && (!meter.value || Number.isInteger(Number(meter.value)) && Number(meter.value) >= 1 && Number(meter.value) <= 32)
  && (!key.value || keyOptions.some(option => option.value === key.value)))
const canSave = computed(() => loaded.value && !!result.value && valid.value && !!(tempo.value || meter.value || key.value))
const elapsed = computed(() => job.value?.started_at ? Math.max(0, Math.floor(((job.value.finished_at ? Date.parse(job.value.finished_at) : now.value) - Date.parse(job.value.started_at)) / 1000)) : null)
const energy = computed(() => {
  const points = result.value?.energy
  if (!points?.length) return null
  return { low: Math.min(...points.map(point => point.rms_dbfs)).toFixed(1), high: Math.max(...points.map(point => point.rms_dbfs)).toFixed(1) }
})
let alive = true, generation = 0
let controller: AbortController | null = null
function clearDraft() { tempo.value = ''; meter.value = ''; key.value = ''; notice.value = '' }
function bytes(value: number | null | undefined): string { if (value == null) return t('musicAnalysis.unknown'); if (value < 1000000) return `${value.toLocaleString()} B`; return value >= 1000000000 ? `${(value / 1000000000).toFixed(1)} GB` : `${(value / 1000000).toFixed(1)} MB` }
function errorText(cause: unknown, fallback = 'failed') { const code = cause instanceof ApiError ? cause.message : ''; return code && te(`musicAnalysis.errors.${code}`) ? t(`musicAnalysis.errors.${code}`) : t(`musicAnalysis.${fallback}`) }
function keyName(value: KeyEstimate | undefined) {
  return value?.status === 'known' && value.tonic_pc != null && value.mode ? t('musicAnalysis.keyName', { note: noteNames[value.tonic_pc], mode: t(`musicAnalysis.modes.${value.mode}`) }) : t('musicAnalysis.unknown')
}
function setJob(value: MusicAnalysisJob | null) {
  if (value && (api.musicSourceKey(value.source) !== sourceKey.value || value.result && api.musicSourceKey(value.result.source) !== sourceKey.value || value.status === 'done' && !value.result)) throw new TypeError('Mismatched analysis result')
  if (value?.result?.id !== job.value?.result?.id) clearDraft()
  job.value = value; now.value = Date.now(); revision.value = value?.result?.override_revision ?? 0
}
const poll = createPollingLoop(async context => {
  const selected = job.value, token = generation
  if (!selected) return false
  try {
    const next = await api.getMusicAnalysis(selected.id, context.signal)
    if (!context.isCurrent() || !alive || token !== generation || !expanded.value) return false
    setJob(next); error.value = ''
  } catch (cause) { if (context.isCurrent() && alive && token === generation) error.value = errorText(cause, 'offline') }
  return active.value
}, 2000)
function stopObserving() { ++generation; controller?.abort(); controller = null; poll.stop(); busy.value = false }
async function perform(action: (context: PollContext) => Promise<void>, fallback = 'failed') {
  if (!alive || busy.value || !expanded.value) return
  stopObserving()
  const token = generation, request = new AbortController(); controller = request
  const context = { signal: request.signal, isCurrent: () => alive && token === generation && expanded.value && !request.signal.aborted }
  busy.value = true; error.value = ''; notice.value = ''
  try { await action(context) } catch (cause) {
    if (context.isCurrent()) {
      if (cause instanceof ApiError && cause.message === 'insufficient_disk') forecastFresh.value = false
      error.value = errorText(cause, fallback)
    }
  }
  finally { if (context.isCurrent()) { busy.value = false; if (active.value) poll.start(false) } }
}
async function refresh() {
  await perform(async context => {
    loaded.value = false; forecastFresh.value = false
    const [jobs, estimate] = await Promise.allSettled([api.listMusicAnalysis(props.source, context.signal), api.previewMusicAnalysis(analysisRequest.value, context.signal)])
    if (!context.isCurrent()) return
    if (jobs.status === 'fulfilled') {
      const matching = jobs.value.filter(item => api.musicSourceKey(item.source) === sourceKey.value)
      setJob(matching.find(item => !item.status || item.status === 'queued' || item.status === 'running') ?? matching[0] ?? null)
      loaded.value = true
    } else error.value = errorText(jobs.reason, 'offline')
    if (estimate.status === 'fulfilled') { forecast.value = estimate.value; forecastFresh.value = true }
    else if (!error.value) error.value = errorText(estimate.reason, 'forecastUnavailable')
  }, 'offline')
}
async function jobAction(action: 'start' | 'cancel' | 'retry') {
  if (action === 'cancel' ? !job.value : !canStart.value || action === 'start' && active.value || action === 'retry' && !job.value) return
  await perform(async context => {
    const current = job.value
    const next = action === 'start' ? await api.startMusicAnalysis(analysisRequest.value, context.signal)
      : current ? await (action === 'cancel' ? api.cancelMusicAnalysis : api.retryMusicAnalysis)(current.id, context.signal) : null
    if (context.isCurrent()) setJob(next)
  })
}
async function save() {
  const current = result.value, currentJob = job.value
  if (!current || !currentJob || !canSave.value) return
  const body: MusicAnalysisOverrides = { analysis_id: current.id, expected_revision: revision.value }
  if (tempo.value) body.tempo = { status: 'known', bpm: Number(tempo.value), confidence: null, confidence_method: 'manual', half_double_ambiguous: false, map: [] }
  if (meter.value) body.meter = { status: 'known', beats_per_bar: Number(meter.value), method: 'manual', confidence: null }
  const selectedKey = keyOptions.find(option => option.value === key.value)
  if (selectedKey) body.key = { status: 'known', tonic_pc: selectedKey.tonic, mode: selectedKey.mode, confidence: null, method: 'manual', candidates: [], unknown_reason: null }
  await perform(async context => {
    try {
      const saved = await api.saveMusicAnalysisOverrides(body, context.signal)
      if (!context.isCurrent()) return
      setJob({ ...currentJob, result: saved }); clearDraft(); notice.value = t('musicAnalysis.saved')
    } catch (cause) {
      if (!context.isCurrent()) return
      if (!(cause instanceof ApiError) || cause.message !== 'analysis_revision_conflict') throw cause
      loaded.value = false
      const latest = await api.getMusicAnalysis(currentJob.id, context.signal)
      if (!context.isCurrent()) return
      setJob(latest); loaded.value = true; error.value = t('musicAnalysis.conflict')
    }
  })
}
watch(sourceKey, () => { stopObserving(); job.value = null; forecast.value = null; forecastFresh.value = false; loaded.value = false; error.value = ''; clearDraft(); if (expanded.value) void refresh() })
watch(expanded, value => { if (value) void refresh(); else stopObserving() })
watch(profile, () => { forecastFresh.value = false; if (expanded.value) void refresh() })
onBeforeUnmount(() => { alive = false; stopObserving() })
</script>

<template>
  <div class="rounded-lg border border-border bg-panel-2 text-xs" data-music-analysis>
    <button type="button" class="flex min-h-11 w-full items-center justify-between px-3 py-2 text-left font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1" :aria-label="t('musicAnalysis.title')" :aria-expanded="expanded" :aria-controls="panelId" @click="expanded = !expanded"><span>{{ t('musicAnalysis.title') }}</span><span aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span></button>
    <div v-if="expanded" :id="panelId" class="space-y-3 border-t border-border p-3" :aria-busy="busy">
      <p class="text-text-dim">{{ t('musicAnalysis.hint') }}</p>
      <label class="block space-y-2 text-text-dim"><span>{{ t('musicAnalysis.profile') }}</span><select v-model="profile" name="analysis-profile" :disabled="busy || active" class="min-h-11 w-full rounded border border-border bg-panel px-3 text-text"><option value="music-v1">{{ t('musicAnalysis.basicProfile') }}</option><option value="music-beats-v1">{{ t('musicAnalysis.beatsProfile') }}</option></select></label>
      <p v-if="profile === 'music-beats-v1'" class="text-text-dim">{{ t('musicAnalysis.beatsProfileHint') }} <a href="/settings#module-music_beats" class="inline-flex min-h-11 items-center text-accent2 underline">{{ t('musicAnalysis.setupBeats') }}</a></p>
      <p v-if="busy && !job" role="status" class="text-text-dim">{{ t('musicAnalysis.loading') }}</p>
      <p v-if="error" role="alert" class="text-status-failed">{{ error }}</p>
      <div v-if="forecast" class="space-y-2 rounded-lg border border-border p-3 text-text-dim">
        <p>{{ t('musicAnalysis.storageHint') }}</p>
        <dl class="grid gap-2 sm:grid-cols-2"><div><dt>{{ t('musicAnalysis.retained') }}</dt><dd class="text-text">{{ bytes(forecast.retained_bytes) }}</dd></div><div><dt>{{ t('musicAnalysis.temporary') }}</dt><dd class="text-text">{{ bytes(forecast.temporary_bytes) }}</dd></div><div><dt>{{ t('musicAnalysis.requiredSpace') }}</dt><dd class="text-text">{{ bytes(forecast.required_free_bytes) }}</dd></div><div><dt>{{ t('musicAnalysis.freeSpace') }}</dt><dd class="text-text">{{ bytes(forecast.free_bytes) }}</dd></div></dl>
        <p v-if="!forecastFresh">{{ t('musicAnalysis.forecastUnavailable') }}</p><p v-else-if="!forecast.can_start" class="text-status-queued">{{ t('musicAnalysis.storageBlocked') }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <button type="button" :disabled="busy" class="min-h-11 text-accent1 underline disabled:opacity-50" @click="refresh">{{ t('musicAnalysis.refresh') }}</button>
        <button v-if="!job || job.status === 'done'" type="button" :disabled="busy || !canStart" class="min-h-11 rounded-lg border border-border px-3 text-text disabled:opacity-50" @click="jobAction('start')">{{ t('musicAnalysis.start') }}</button>
        <button v-else-if="active" type="button" :disabled="busy || job.cancellation_requested" class="min-h-11 rounded-lg border border-border px-3 text-text disabled:opacity-50" @click="jobAction('cancel')">{{ t('musicAnalysis.cancel') }}</button>
        <button v-else type="button" :disabled="busy || !canStart" class="min-h-11 rounded-lg border border-border px-3 text-text disabled:opacity-50" @click="jobAction('retry')">{{ t('musicAnalysis.retry') }}</button>
      </div>
      <div v-if="job" class="space-y-1 text-text-dim" role="status">
        <p>{{ t(`musicAnalysis.states.${job.status ?? 'queued'}`) }} · {{ t(`musicAnalysis.phases.${job.phase ?? 'waiting'}`) }}<span v-if="elapsed !== null"> · {{ t('musicAnalysis.elapsed', { seconds: elapsed }) }}</span></p>
        <p v-if="job.progress">{{ t('musicAnalysis.progress', { current: job.progress.current, total: job.progress.total, unit: t(`musicAnalysis.units.${job.progress.unit}`) }) }}</p>
        <p v-if="job.cancellation_requested">{{ t('musicAnalysis.cancellationRequested') }}</p>
        <p v-if="job.error_code" class="text-status-failed">{{ t(`musicAnalysis.errors.${job.error_code}`) }}</p>
      </div>
      <div v-if="result" class="space-y-3">
        <p class="text-text-dim">{{ t(estimatedBeats ? 'musicAnalysis.beatsLimits' : 'musicAnalysis.limits') }}</p>
        <ul v-if="result.warnings?.length" class="space-y-1 text-status-queued"><li v-for="warning in result.warnings" :key="warning">{{ t(`musicAnalysis.warnings.${warning}`) }}</li></ul>
        <ul class="grid gap-2 text-text sm:grid-cols-2">
          <li>{{ t('musicAnalysis.tempo') }}: {{ result.tempo?.status === 'known' && result.tempo.bpm != null ? t('musicAnalysis.bpm', { value: result.tempo.bpm }) : t('musicAnalysis.unknown') }}<span v-if="result.tempo?.status === 'known'" class="ml-2 text-text-dim">{{ t(result.tempo.confidence_method === 'manual' ? 'musicAnalysis.manual' : 'musicAnalysis.estimated') }}</span></li>
          <li>{{ t('musicAnalysis.meter') }}: {{ result.meter?.status === 'known' ? result.meter.beats_per_bar : t('musicAnalysis.unknown') }}<span v-if="result.meter?.status === 'known'" class="ml-2 text-text-dim">{{ t(result.meter.method === 'manual' ? 'musicAnalysis.manual' : 'musicAnalysis.estimated') }}</span></li>
          <li>{{ t('musicAnalysis.beatGrid') }}: {{ result.beats_sec?.length || t('musicAnalysis.unknown') }}</li>
          <li>{{ t('musicAnalysis.downbeats') }}: {{ result.downbeats_sec?.length || t('musicAnalysis.unknown') }}</li>
          <li>{{ t('musicAnalysis.vocals') }}: {{ result.vocal_activity?.status === 'known' ? result.vocal_activity.intervals?.length : t('musicAnalysis.unknown') }}</li>
          <li>{{ t('musicAnalysis.key') }}: {{ keyName(result.key) }}<span v-if="result.key?.status === 'known'" class="ml-2 text-text-dim">{{ t(result.key.method === 'manual' ? 'musicAnalysis.manual' : 'musicAnalysis.estimated') }}</span><p v-if="result.key?.unknown_reason" class="text-text-dim">{{ t(`musicAnalysis.keyReasons.${result.key.unknown_reason}`) }}</p></li>
          <li>{{ t('musicAnalysis.changes') }}: {{ result.key_changes?.length || t('musicAnalysis.unknown') }}</li>
        </ul>
        <div v-if="result.key?.candidates?.length" class="text-text-dim"><p>{{ t('musicAnalysis.candidates') }}</p><ul class="flex flex-wrap gap-3"><li v-for="candidate in result.key.candidates" :key="`${candidate.tonic_pc}:${candidate.mode}`">{{ t('musicAnalysis.keyName', { note: noteNames[candidate.tonic_pc], mode: t(`musicAnalysis.modes.${candidate.mode}`) }) }} · {{ candidate.score.toFixed(2) }}</li></ul></div>
        <p v-if="energy" class="text-text-dim">{{ t('musicAnalysis.energy') }}: {{ t('musicAnalysis.energyRange', energy) }}</p>
        <div v-if="result.sections?.length" class="space-y-1 text-text-dim"><p class="font-medium text-text">{{ t('musicAnalysis.sections') }}</p><p>{{ t('musicAnalysis.sectionsHint') }}</p><ol class="flex flex-wrap gap-3"><li v-for="section in result.sections" :key="section.id">{{ section.label }} · {{ t('musicAnalysis.interval', { start: section.start_sec.toFixed(1), end: section.end_sec.toFixed(1) }) }}</li></ol></div>
        <AudioQualityReport :metrics="result.loudness" />
        <details class="rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer py-3 font-medium text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t('musicAnalysis.corrections') }}</summary>
          <form class="space-y-3" @submit.prevent="save"><p class="text-text-dim">{{ t('musicAnalysis.correctionsHint') }}</p><div class="grid gap-3 sm:grid-cols-3">
            <label class="space-y-1 text-text-dim"><span class="block">{{ t('musicAnalysis.manualTempo') }}</span><input v-model="tempo" type="number" min="20" max="400" step="any" :aria-label="t('musicAnalysis.manualTempo')" :disabled="busy" class="min-h-11 w-full rounded border border-border bg-panel p-2 text-text" /></label>
            <label class="space-y-1 text-text-dim"><span class="block">{{ t('musicAnalysis.manualMeter') }}</span><input v-model="meter" type="number" min="1" max="32" step="1" :aria-label="t('musicAnalysis.manualMeter')" :disabled="busy" class="min-h-11 w-full rounded border border-border bg-panel p-2 text-text" /></label>
            <label class="space-y-1 text-text-dim"><span class="block">{{ t('musicAnalysis.manualKey') }}</span><select v-model="key" name="analysis-manual-key" :disabled="busy" class="min-h-11 w-full rounded border border-border bg-panel p-2 text-text"><option value="">{{ t('musicAnalysis.unchanged') }}</option><option v-for="option in keyOptions" :key="option.value" :value="option.value">{{ t('musicAnalysis.keyName', { note: option.note, mode: t(`musicAnalysis.modes.${option.mode}`) }) }}</option></select></label>
          </div><p v-if="!valid" role="alert" class="text-status-failed">{{ t('musicAnalysis.invalid') }}</p><button type="submit" :disabled="busy || !canSave" class="min-h-11 rounded-lg border border-border px-3 text-text disabled:opacity-50">{{ t(busy ? 'musicAnalysis.saving' : 'musicAnalysis.save') }}</button></form>
        </details>
      </div>
      <p v-if="notice" role="status" class="text-text-dim">{{ notice }}</p>
    </div>
  </div>
</template>
