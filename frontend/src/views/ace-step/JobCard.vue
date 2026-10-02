<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAceStepStore } from '../../stores/aceStep'
import { isVoiceReplacement, type AceJob } from '../../stores/aceStep'
import { trackAudioUrl } from '../../api/tracks'
import { formatDuration } from '../../composables/formatDuration'
import StatusBadge from '../../components/shared/StatusBadge.vue'
import ProgressBar from '../../components/shared/ProgressBar.vue'
import WaveformPlayer from '../../components/shared/WaveformPlayer.vue'
import TrackAudioVersions from '../../components/shared/TrackAudioVersions.vue'
import FavoriteTrackButton from '../../components/shared/FavoriteTrackButton.vue'
import BatchABPlayer from '../../components/shared/BatchABPlayer.vue'
import StemsPanel from '../../components/shared/StemsPanel.vue'
import MidiPanel from '../../components/shared/MidiPanel.vue'
import EditableTitle from '../../components/shared/EditableTitle.vue'
import { voiceErrorText, VoiceApplyError } from '../../api/voices'
import { ApiError } from '../../api/http'
import VoiceApplyStatus from '../../components/shared/VoiceApplyStatus.vue'
import TrackDetails from '../../components/shared/TrackDetails.vue'
import StyleChips from '../../components/shared/StyleChips.vue'
import { formatCreated } from '../../composables/formatCreated'
import { friendlyTitle } from '../../utils/trackTitle'

const props = defineProps<{ job: AceJob, number: string, view: 'cards' | 'list'; visibleTrackIds?: readonly number[]; favoritesOnly?: boolean; processingActive?: boolean }>()
const emit = defineEmits<{ playing: [value: boolean]; processing: [value: boolean] }>()
const playingSources = ref<Set<string>>(new Set())
const processingTracks = ref<Set<number>>(new Set())
function notePlaying(source: string, value: boolean): void {
  const next = new Set(playingSources.value)
  if (value) next.add(source); else next.delete(source)
  playingSources.value = next; emit('playing', next.size > 0)
}
function noteProcessing(trackId: number, value: boolean): void {
  const next = new Set(processingTracks.value)
  if (value) next.add(trackId); else next.delete(trackId)
  processingTracks.value = next; emit('processing', next.size > 0)
}
const deleteArmed = ref(false)
let disarmTimer: ReturnType<typeof setTimeout> | undefined
let copiedTimer: ReturnType<typeof setTimeout> | undefined
let mounted = true
function disarmDelete(): void { deleteArmed.value = false; clearTimeout(disarmTimer) }
watch(() => props.job.id, disarmDelete)
onBeforeUnmount(() => { mounted = false; disarmDelete(); clearTimeout(copiedTimer); emit('playing', false); emit('processing', false) })
const store = useAceStepStore()
const { t } = useI18n()
const showDetails = ref(false)
const rowOpen = ref(false)
const copied = ref(false)
const actionError = ref('')
const actionPending = ref(false)
const savedTracks = computed(() => props.job.dbIds.map((id, index) => ({ id, index })).filter((track) => !props.visibleTrackIds || props.visibleTrackIds.includes(track.id) || playingSources.value.has(`version:${track.id}`) || processingTracks.value.has(track.id)))
const singleSavedTrack = computed(() => props.job.dbIds.length === 1 ? savedTracks.value[0] : undefined)
function favoriteLabel(index: number): string {
  const title = props.job.title || t('aceJob.noDescription')
  return props.job.dbIds.length > 1 ? `${title} · ${t('trackAudio.variant', { index: index + 1 })}` : title
}

const showBody = computed(() => props.view === 'cards' || rowOpen.value || playingSources.value.size > 0 || props.processingActive || processingTracks.value.size > 0)
const voiceRunning = computed(() => props.job.voiceApply === 'running')
const replacement = computed(() => isVoiceReplacement(props.job))
const sourceTrackId = computed(() => {
  const id = props.job.params?.source_track_id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? id : null
})
const replacementCancelled = computed(() => replacement.value && props.job.voiceErrorCode === 'cancelled')
const shownStatus = computed(() => voiceRunning.value ? props.job.voiceProgress?.status === 'queued' ? 'queued' : 'running' : replacement.value && props.job.voiceApply === 'failed' ? replacementCancelled.value ? 'cancelled' : 'failed' : props.job.status)

const summary = computed(() => {
  const params = props.job.params || {}
  const bits: string[] = []
  if (props.job.durationSec) bits.push(formatDuration(props.job.durationSec))
  if (params.bpm) bits.push(`${params.bpm} BPM`)
  if (params.key_scale) bits.push(String(params.key_scale))
  if (params.time_signature) bits.push(String(params.time_signature))
  if (params.vocal_language) bits.push(String(params.vocal_language))
  bits.push(replacement.value ? t('aceGen.replacementResult') : props.job.model || t('aceJob.defaultModel'))
  if (props.job.voiceApply === 'running') {
    bits.push(props.job.voiceName ? t('voiceClone.applyingNamed', { name: props.job.voiceName }) : t('voiceClone.applyingShort'))
  } else if (props.job.voiceApply === 'done') {
    bits.push(props.job.voiceName ? t('trackAudio.initialVoice', { name: props.job.voiceName }) : t('trackAudio.initialVoiceGeneric'))
  }
  return bits.join(' · ')
})

const detailRows = computed(() => {
  const params = props.job.params || {}
  const rows: { label: string, value: string }[] = []
  const add = (label: string, value: unknown) => {
    if (value == null || value === '') return
    rows.push({ label, value: String(value) })
  }
  add(t('aceJob.trackId'), props.number)
  add(t('aceJob.duration'), props.job.durationSec ? formatDuration(props.job.durationSec) : '')
  add(t('aceJob.model'), props.job.model)
  add(t('aceJob.bpm'), params.bpm)
  add(t('aceJob.key'), params.key_scale)
  add(t('aceJob.time'), params.time_signature)
  add(t('aceJob.language'), params.vocal_language)
  add(t('aceJob.seed'), params.seed)
  add(t('aceJob.steps'), params.inference_steps)
  add(t('aceJob.guidance'), params.guidance_scale)
  add(t('aceJob.task'), replacement.value ? t('aceGen.replacementResult') : params.task_type)
  add(t('aceJob.format'), props.job.audioFormat)
  return rows
})

const created = computed(() => formatCreated(props.job.createdAt))
const shortTitle = computed(() => friendlyTitle(props.job.title))
// Custom-mode style tags land in params.prompt, Simple-mode ones in
// params.sample_query (or params.prompt when a reference track is attached) -
// the title itself is truncated to 60 chars at submit time, so this is the
// only place the full, untruncated style text is still available.
const styleText = computed(() => {
  const p = props.job.params || {}
  return typeof p.prompt === 'string' && p.prompt ? p.prompt : (typeof p.sample_query === 'string' ? p.sample_query : '')
})

async function perform(action: () => Promise<void>, failureMessage?: string) {
  if (actionPending.value) return
  actionError.value = ''
  actionPending.value = true
  try { await action() } catch (error) {
    if (!mounted) return
    actionError.value = failureMessage || (error instanceof VoiceApplyError
      ? voiceErrorText(error.code, error.detail)
      : replacement.value && error instanceof ApiError
        ? voiceErrorText(error.message)
        : error instanceof Error ? error.message : t('storeErrors.unknownError'))
  }
  finally { if (mounted) actionPending.value = false }
}
function cancel() { void perform(() => store.cancel(props.job.id)) }
function remove(): void {
  if (actionPending.value || props.job.voiceActionPending) return
  if (!deleteArmed.value) { deleteArmed.value = true; clearTimeout(disarmTimer); disarmTimer = setTimeout(disarmDelete, 4000); return }
  disarmDelete(); void perform(() => store.removeJob(props.job.id), t('upstreamLibrary.deleteFailed'))
}
function rename(title: string): void { void perform(() => store.renameJob(props.job.id, title), t('upstreamLibrary.actionFailed')) }
function noteBatch(event: Event, value: boolean): void { if (event.target instanceof HTMLAudioElement) notePlaying(`batch:${event.target.src}`, value) }
function retrySave() { void perform(() => store.retrySave(props.job.id)) }
function retryReplacement() { void perform(() => store.retryVoiceReplacement(props.job.id)) }
function download(url: string, index: number) {
  const a = document.createElement('a')
  a.href = url
  const ext = props.job.voiceApply === 'done' ? 'wav' : props.job.audioFormat
  a.download = `${(props.job.title || 'track').replace(/[^\w\-]+/g, '_')}_${index + 1}.${ext}`
  a.click()
}
function copyParamsToForm() {
  const p = props.job.params || {}
  store.requestInsertParams({
    ...p,
    lyrics: props.job.lyrics || p.lyrics || '',
    model: props.job.model || p.model || '',
    audio_format: props.job.audioFormat || p.audio_format,
    query: props.job.title || p.query || '',
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => (copied.value = false), 2000)
}
</script>

<template>
  <div :class="view === 'list' ? 'bg-panel' : 'space-y-3 rounded-xl border border-border bg-panel p-4'">
    <button
      v-if="view === 'list'"
      type="button"
      class="flex w-full items-center gap-3 px-3 py-2 text-left"
      @click="rowOpen = !rowOpen"
    >
      <span class="w-16 shrink-0 text-right text-sm tabular-nums text-text-dim">{{ number }}</span>
      <span class="min-w-0 flex-1">
        <span class="block truncate text-sm font-medium text-text">{{ job.title || t('aceJob.noDescription') }}</span>
        <span class="block truncate text-xs text-text-dim">{{ summary }}</span>
      </span>
      <span class="shrink-0 text-xs text-text-dim">{{ rowOpen ? t('aceJob.hideDetails') : t('aceJob.showDetails') }}</span>
    </button>
    <div v-if="view === 'list' && savedTracks.length" class="flex flex-wrap items-center gap-3 px-3 pb-2">
      <div v-for="track in savedTracks" :key="track.id" class="inline-flex items-center gap-1.5">
        <span v-if="job.dbIds.length > 1" class="text-xs text-text-dim">{{ t('trackAudio.variant', { index: track.index + 1 }) }}</span>
        <FavoriteTrackButton :track-id="track.id" :label="favoriteLabel(track.index)" />
      </div>
    </div>
    <VoiceApplyStatus
      v-if="voiceRunning && view === 'list' && !showBody"
      class="px-3 pb-2"
      :phase="job.voicePhase"
      :voice-name="job.voiceName"
      :job-progress="job.voiceProgress"
    />
    <div v-if="showBody" :class="view === 'list' ? 'space-y-3 px-3 pb-3' : 'contents'">
    <div v-if="view === 'cards'" class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0 flex-1 basis-64">
        <div class="flex items-start gap-2">
          <span class="mt-0.5 shrink-0 rounded-md border border-border px-1.5 py-0.5 text-xs tabular-nums text-text-dim">{{ number }}</span>
          <EditableTitle
            :model-value="job.title"
            :display-text="shortTitle"
            :placeholder="t('aceJob.noDescription')"
            :editable="job.dbIds.length > 0"
            @rename="rename"
          />
        </div>
        <p class="mt-2 text-xs leading-relaxed text-text-dim">{{ summary }} · <span data-created :title="created.full">{{ created.label }}</span></p>
        <StyleChips class="mt-2" :text="styleText" :max="4" @more="showDetails = true" />
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <FavoriteTrackButton v-if="singleSavedTrack" :track-id="singleSavedTrack.id" :label="favoriteLabel(singleSavedTrack.index)" />
        <StatusBadge :status="shownStatus" :label="voiceRunning && shownStatus !== 'queued' ? t('voiceClone.applyingBadge') : undefined" />
        <button v-if="voiceRunning && replacement || !voiceRunning && (job.status === 'queued' || job.status === 'running')" type="button" :disabled="actionPending || job.voiceActionPending" class="min-h-9 min-w-9 rounded-lg px-2 text-text-dim hover:bg-panel-2 hover:text-status-failed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1" :title="t('aceJob.cancel')" :aria-label="t('aceJob.cancel')" @click="cancel">⏹</button>
        <button v-else type="button" :disabled="actionPending || job.voiceActionPending" class="min-h-9 min-w-9 rounded-lg px-2 text-text-dim hover:bg-panel-2 hover:text-status-failed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1" :title="deleteArmed ? t('upstreamLibrary.confirmDelete') : t('aceJob.delete')" :aria-label="deleteArmed ? t('upstreamLibrary.confirmDelete') : t('aceJob.delete')" @blur="disarmDelete" @click="remove"><span v-if="deleteArmed">{{ t('upstreamLibrary.confirmDelete') }}</span><span v-else aria-hidden="true">✕</span></button>
      </div>
    </div>

    <VoiceApplyStatus
      v-if="voiceRunning && view === 'cards' && !savedTracks.length"
      :phase="job.voicePhase"
      :voice-name="job.voiceName"
      :job-progress="job.voiceProgress"
    />

    <div v-else-if="!voiceRunning && (job.status === 'queued' || job.status === 'running')" class="space-y-1">
      <ProgressBar :value="job.progress" />
      <p class="text-xs text-text-dim">{{ job.stage || (job.status === 'queued' ? t('aceJob.queued') : t('aceJob.generating')) }}</p>
    </div>

    <div v-else-if="job.status === 'failed'" class="space-y-2 rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">
      <p>{{ job.error }}</p>
      <button v-if="job.backendOwned && job.errorCode === 'save_failed'" type="button" class="text-accent1 hover:underline disabled:opacity-50" :disabled="actionPending" @click="retrySave">{{ t('aceJob.retrySave') }}</button>
    </div>
    <div v-else-if="job.status === 'cancelled'" class="rounded-lg bg-panel-2 p-2 text-xs text-text-dim">{{ t('aceJob.cancelled') }}</div>

    <p v-if="actionError" class="text-xs text-status-failed" role="alert">{{ actionError }}</p>
    <p v-if="job.voiceApply === 'failed'" role="alert" :class="replacementCancelled ? 'text-xs text-text-dim' : 'text-xs text-status-failed'">{{ replacementCancelled ? t('aceGen.replacementCancelled') : voiceErrorText(job.voiceErrorCode || '', job.voiceError || '') }}</p>
    <div v-if="job.dbIds.length > 0 || job.audioUrls.length > 0 && (!replacement || job.voiceApply === 'done')" class="space-y-2">
      <p v-if="job.voiceApply === 'done' && !job.dbIds.length" class="text-xs text-status-done">
        {{ job.voiceName ? t('voiceClone.appliedNamed', { name: job.voiceName }) : t('voiceClone.applied') }}
      </p>
      <div v-if="(!favoritesOnly || [...playingSources].some(source => source.startsWith('batch:'))) && job.audioUrls && job.audioUrls.length > 1" @play.capture="noteBatch($event, true)" @pause.capture="noteBatch($event, false)" @ended.capture="noteBatch($event, false)"><BatchABPlayer :sources="job.audioUrls" :duration-sec="job.durationSec" /></div>
      <WaveformPlayer v-else-if="!job.dbIds?.length && job.audioUrls?.length === 1" :src="job.audioUrls[0]" @playing="notePlaying('waveform', $event)" />
      <div v-for="track in savedTracks" :key="'versions' + track.id" class="space-y-2">
        <div v-if="job.dbIds.length > 1" class="flex flex-wrap items-center justify-between gap-2"><p class="text-xs font-medium text-text-dim">{{ t('trackAudio.variant', { index: track.index + 1 }) }}</p><FavoriteTrackButton v-if="view === 'cards'" :track-id="track.id" :label="favoriteLabel(track.index)" /></div>
        <TrackAudioVersions :track-id="track.id" :fallback-audio-url="!replacement || job.voiceApply === 'done' ? job.audioUrls?.[track.index] : null" :voice-applying="voiceRunning" @playing="notePlaying(`version:${track.id}`, $event)" @processing="noteProcessing(track.id, $event)" />
      </div>

      <div class="flex flex-wrap items-center gap-3 text-xs text-text-dim">
        <span v-if="job.durationSec">{{ formatDuration(job.durationSec) }}</span>
        <button v-for="(url, i) in !job.dbIds?.length ? job.audioUrls : []" :key="'dl' + url" type="button" class="text-accent1 hover:underline" @click="download(url, i)">
          {{ t('aceJob.download') }}{{ job.audioUrls.length > 1 ? ` #${i + 1}` : '' }}
        </button>
      </div>
      <div v-for="track in savedTracks" :key="'stems' + track.id" class="space-y-1.5 pt-1">
        <p class="text-xs text-text-dim">{{ t('trackAudio.analysisHint') }}</p>
        <StemsPanel :track-id="track.id" :title="job.title" :lyrics="job.lyrics" :model="job.origin ?? 'ace_step'" />
        <MidiPanel :track-id="track.id" />
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-3">
      <a v-if="sourceTrackId" :href="trackAudioUrl(sourceTrackId)" download class="text-xs text-accent1 hover:underline">{{ t('aceGen.replacementSource') }}</a>
      <button v-if="replacement && job.voiceApply === 'failed'" type="button" :disabled="actionPending || job.voiceActionPending" class="text-xs text-accent1 hover:underline disabled:opacity-50" @click="retryReplacement">{{ t('aceGen.replacementRetry') }}</button>
      <button v-if="!replacement" type="button" class="text-xs text-accent hover:underline" @click="copyParamsToForm">
        {{ copied ? t('aceJob.copied') : t('aceJob.copyParams') }}
      </button>
      <button v-if="view === 'list' && (voiceRunning && replacement || !voiceRunning && (job.status === 'queued' || job.status === 'running'))" type="button" :disabled="actionPending || job.voiceActionPending" class="text-xs text-text-dim hover:text-status-failed" @click="cancel">{{ t('aceJob.cancel') }}</button>
      <button v-else-if="view === 'list'" type="button" :disabled="actionPending || job.voiceActionPending" class="text-xs text-text-dim hover:text-status-failed" @blur="disarmDelete" @click="remove">{{ t(deleteArmed ? 'upstreamLibrary.confirmDelete' : 'aceJob.delete') }}</button>
      <button v-if="view === 'cards' && (job.lyrics || styleText || detailRows.length)" type="button" class="text-xs text-text-dim hover:underline" @click="showDetails = !showDetails">
        {{ showDetails ? t('aceJob.hideDetails') : t('aceJob.showDetails') }}
      </button>
    </div>
    <div v-if="showDetails || view === 'list'" class="space-y-2 rounded-lg bg-panel-2 p-2 text-xs text-text-dim">
      <dl v-if="detailRows.length" class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <template v-for="row in detailRows" :key="row.label">
          <dt>{{ row.label }}</dt>
          <dd class="text-text">{{ row.value }}</dd>
        </template>
      </dl>
      <TrackDetails :style-text="styleText" :lyrics="job.lyrics" />
    </div>
    </div>
  </div>
</template>
