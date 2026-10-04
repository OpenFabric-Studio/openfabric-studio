<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useYue2Store } from '../../stores/yue2'
import type { Yue2Job } from '../../stores/yue2'
import * as tracksApi from '../../api/tracks'
import { formatDuration } from '../../composables/formatDuration'
import StatusBadge from '../../components/shared/StatusBadge.vue'
import WaveformPlayer from '../../components/shared/WaveformPlayer.vue'
import TrackAudioVersions from '../../components/shared/TrackAudioVersions.vue'
import FavoriteTrackButton from '../../components/shared/FavoriteTrackButton.vue'
import StemsPanel from '../../components/shared/StemsPanel.vue'
import MidiPanel from '../../components/shared/MidiPanel.vue'
import EditableTitle from '../../components/shared/EditableTitle.vue'
import { voiceErrorText } from '../../api/voices'
import VoiceApplyStatus from '../../components/shared/VoiceApplyStatus.vue'
import YueGenerationStatus from '../../components/shared/YueGenerationStatus.vue'
import TrackDetails from '../../components/shared/TrackDetails.vue'
import StyleChips from '../../components/shared/StyleChips.vue'
import { formatCreated } from '../../composables/formatCreated'
import { friendlyTitle } from '../../utils/trackTitle'

const props = defineProps<{ job: Yue2Job, number: string, view: 'cards' | 'list'; processingActive?: boolean }>()
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
const store = useYue2Store()
const { t } = useI18n()
const showDetails = ref(false)
const rowOpen = ref(false)
const showBody = computed(() => props.view === 'cards' || rowOpen.value || playingSources.value.size > 0 || props.processingActive || processingTracks.value.size > 0)
const voiceRunning = computed(() => props.job.voiceApply === 'running')
const shownStatus = computed(() => voiceRunning.value ? props.job.voiceProgress?.status === 'queued' ? 'queued' : 'running' : props.job.status)

const summary = computed(() => {
  const bits: string[] = []
  if (props.job.durationSec) bits.push(formatDuration(props.job.durationSec))
  bits.push(`cot: ${props.job.cot}`)
  bits.push(props.job.precision)
  bits.push(`seed ${props.job.seed}`)
  if (props.job.voiceApply === 'running') {
    bits.push(props.job.voiceName ? t('voiceClone.applyingNamed', { name: props.job.voiceName }) : t('voiceClone.applyingShort'))
  } else if (props.job.voiceApply === 'done') {
    bits.push(props.job.voiceName ? t('trackAudio.initialVoice', { name: props.job.voiceName }) : t('trackAudio.initialVoiceGeneric'))
  }
  return bits.join(' · ')
})

const detailRows = computed(() => {
  const rows: { label: string, value: string }[] = []
  const add = (label: string, value: unknown) => {
    if (value == null || value === '') return
    rows.push({ label, value: String(value) })
  }
  add(t('aceJob.trackId'), props.number)
  add(t('aceJob.duration'), props.job.durationSec ? formatDuration(props.job.durationSec) : '')
  add(t('aceJob.seed'), props.job.seed)
  add('cot', props.job.cot)
  add(t('aceJob.format'), props.job.precision)
  return rows
})
const showAbc = ref(false)
const abcText = ref<string | null>(null)
const loadingAbc = ref(false)
const copied = ref(false)
const actionPending = ref(false)
const actionError = ref('')

const created = computed(() => formatCreated(props.job.createdAt))
const shortTitle = computed(() => friendlyTitle(props.job.title))

async function perform(action: () => void | Promise<void>, message = t('upstreamLibrary.actionFailed')): Promise<void> {
  if (actionPending.value) return
  actionPending.value = true; actionError.value = ''
  try { await action() } catch { if (mounted) actionError.value = message } finally { if (mounted) actionPending.value = false }
}
function cancel(): void { void perform(() => store.cancel(props.job.id)) }
function remove(): void {
  if (actionPending.value) return
  if (!deleteArmed.value) { deleteArmed.value = true; clearTimeout(disarmTimer); disarmTimer = setTimeout(disarmDelete, 4000); return }
  disarmDelete(); void perform(() => store.deleteJob(props.job), t('upstreamLibrary.deleteFailed'))
}
function rename(title: string): void { void perform(() => store.renameJob(props.job, title)) }
async function toggleAbc() {
  showAbc.value = !showAbc.value
  if (showAbc.value && abcText.value == null) {
    if (props.job.abcPlan) {
      abcText.value = props.job.abcPlan
    } else if (props.job.dbId != null) {
      loadingAbc.value = true
      try {
        abcText.value = await tracksApi.trackAbc(props.job.dbId)
      } catch {
        abcText.value = ''
      } finally {
        loadingAbc.value = false
      }
    } else {
      abcText.value = ''
    }
  }
}
function insertIntoForm() {
  if (abcText.value) store.requestInsertAbc(abcText.value)
}
function download() {
  if (!props.job.audioUrl) return
  const a = document.createElement('a')
  a.href = props.job.audioUrl
  a.download = props.job.savedFilename || `yue2_${props.job.seed}.wav`
  a.click()
}
function copyParamsToForm() {
  const p = props.job.params || {}
  store.requestInsertParams({
    ...p,
    lyrics: props.job.lyrics || p.lyrics || '',
    style: props.job.style || p.style || '',
    song_title: props.job.title,
    cot: props.job.cot || p.cot || 'off',
    precision: props.job.precision || p.precision || 'q8_0',
    seed: props.job.seed ?? p.seed,
    abc: props.job.abcPlan || p.abc || '',
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
        <span class="block truncate text-sm font-medium text-text">{{ job.title || t('yueTrack.noStyle') }}</span>
        <span class="block truncate text-xs text-text-dim">{{ summary }}</span>
      </span>
      <span class="shrink-0 text-xs text-text-dim">{{ rowOpen ? t('aceJob.hideDetails') : t('aceJob.showDetails') }}</span>
    </button>
    <div v-if="view === 'list' && job.dbId != null" class="px-3 pb-2"><FavoriteTrackButton :track-id="job.dbId" :label="job.title || t('yueTrack.noStyle')" /></div>
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
            :placeholder="t('yueTrack.noStyle')"
            :editable="job.dbId != null"
            @rename="rename"
          />
        </div>
        <p class="mt-2 text-xs leading-relaxed text-text-dim">{{ summary }} · <span data-created :title="created.full">{{ created.label }}</span></p>
        <StyleChips class="mt-2" :text="job.style" :max="4" @more="showDetails = true" />
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <FavoriteTrackButton v-if="job.dbId != null" :track-id="job.dbId" :label="job.title || t('yueTrack.noStyle')" />
        <StatusBadge :status="shownStatus" :label="voiceRunning && shownStatus !== 'queued' ? t('voiceClone.applyingBadge') : undefined" />
        <button v-if="!voiceRunning && (job.status === 'queued' || job.status === 'running' || job.status === 'stopping' || job.errorCode === 'engine_recovery_required')" type="button" class="min-h-9 min-w-9 rounded-lg px-2 text-text-dim hover:bg-panel-2 hover:text-status-failed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1" :disabled="actionPending" :aria-label="t('aceJob.cancel')" :title="t('aceJob.cancel')" @click="cancel">⏹</button>
        <button v-else type="button" class="min-h-9 min-w-9 rounded-lg px-2 text-text-dim hover:bg-panel-2 hover:text-status-failed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1" :disabled="actionPending" :aria-label="deleteArmed ? t('upstreamLibrary.confirmDelete') : t('aceJob.delete')" :title="deleteArmed ? t('upstreamLibrary.confirmDelete') : t('aceJob.delete')" @blur="disarmDelete" @click="remove"><span v-if="deleteArmed">{{ t('upstreamLibrary.confirmDelete') }}</span><span v-else aria-hidden="true">✕</span></button>
      </div>
    </div>

    <VoiceApplyStatus
      v-if="voiceRunning && view === 'cards' && job.dbId == null"
      :phase="job.voicePhase"
      :voice-name="job.voiceName"
      :job-progress="job.voiceProgress"
    />

    <div v-else-if="!voiceRunning && (job.status === 'queued' || job.status === 'running' || job.status === 'stopping' || job.errorCode === 'engine_recovery_required')" class="space-y-1">
      <YueGenerationStatus :stage="job.stage" :elapsed-seconds="job.elapsedSeconds" :phase-eta-seconds="job.phaseEtaSeconds" :progress="job.nativeProgress" :native-progress-available="job.nativeProgressAvailable" :stopping="job.status === 'stopping'" />
      <p class="text-xs text-text-dim">seed: {{ job.seed }}</p>
    </div>

    <div v-else-if="job.status === 'failed'" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ job.error }}</div>
    <div v-else-if="job.status === 'cancelled'" class="rounded-lg bg-panel-2 p-2 text-xs text-text-dim">{{ t('aceJob.cancelled') }}</div>

    <p v-if="actionError" role="alert" class="text-xs text-status-failed">{{ actionError }}</p>
    <div v-if="job.status === 'done' && (job.audioUrl || job.dbId != null)" class="space-y-2">
      <p v-if="job.voiceApply === 'failed'" class="text-xs text-status-failed">{{ voiceErrorText(job.voiceErrorCode || '', job.voiceError || '') }}</p>
      <p v-else-if="job.voiceApply === 'done' && job.dbId == null" class="text-xs text-status-done">
        {{ job.voiceName ? t('voiceClone.appliedNamed', { name: job.voiceName }) : t('voiceClone.applied') }}
      </p>
      <TrackAudioVersions v-if="job.dbId != null" :track-id="job.dbId" :fallback-audio-url="job.audioUrl" :fallback-filename="job.savedFilename" :voice-applying="voiceRunning" @playing="notePlaying('version', $event)" @processing="noteProcessing(job.dbId, $event)" />
      <WaveformPlayer v-else-if="job.audioUrl" :src="job.audioUrl" @playing="notePlaying('waveform', $event)" />
      <div class="flex flex-wrap items-center gap-3 text-xs text-text-dim">
        <span v-if="job.durationSec">{{ formatDuration(job.durationSec) }}</span>
        <span v-if="job.wallSec">{{ t('yueTrack.generationTime', { value: job.wallSec.toFixed(1) }) }}</span>
        <span>seed: {{ job.seed }}</span>
        <button v-if="job.dbId == null" type="button" class="text-accent1 hover:underline" @click="download">{{ t('aceJob.download') }}</button>
        <span v-if="job.savedFilename" class="break-all">💾 {{ job.savedFilename }}</span>
        <span v-else-if="job.saveError" class="text-status-failed" :title="job.saveError">{{ t('yueTrack.notSaved') }}</span>
      </div>
      <div v-if="job.dbId != null" class="space-y-1.5 pt-1">
        <p class="text-xs text-text-dim">{{ t('trackAudio.analysisHint') }}</p>
        <StemsPanel :track-id="job.dbId" :title="job.title" :lyrics="job.lyrics" model="yue2" />
        <MidiPanel :track-id="job.dbId" />
      </div>
      <button type="button" class="text-xs text-text-dim hover:underline" @click="toggleAbc">
        {{ showAbc ? t('yueTrack.hideAbc') : t('yueTrack.showAbc') }}
      </button>
      <div v-if="showAbc">
        <p v-if="loadingAbc" class="text-xs text-text-dim">{{ t('yueTrack.loading') }}</p>
        <template v-else>
          <pre class="whitespace-pre-wrap rounded-lg bg-panel-2 p-2 text-xs text-text-dim">{{ abcText || t('yueTrack.noScore') }}</pre>
          <button v-if="abcText" type="button" class="mt-1 text-xs text-accent1 hover:underline" @click="insertIntoForm">{{ t('yueTrack.insertIntoForm') }}</button>
        </template>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-3">
      <button type="button" class="text-xs text-accent hover:underline" @click="copyParamsToForm">
        {{ copied ? t('aceJob.copied') : t('aceJob.copyParams') }}
      </button>
      <button v-if="view === 'list' && !voiceRunning && (job.status === 'queued' || job.status === 'running' || job.status === 'stopping' || job.errorCode === 'engine_recovery_required')" type="button" class="text-xs text-text-dim hover:text-status-failed" @click="cancel">{{ t('aceJob.cancel') }}</button>
      <button v-else-if="view === 'list'" type="button" class="text-xs text-text-dim hover:text-status-failed" @blur="disarmDelete" @click="remove">{{ t(deleteArmed ? 'upstreamLibrary.confirmDelete' : 'aceJob.delete') }}</button>
      <button v-if="view === 'cards' && (job.lyrics || detailRows.length)" type="button" class="text-xs text-text-dim hover:underline" @click="showDetails = !showDetails">
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
      <TrackDetails :style-text="job.style" :lyrics="job.lyrics" />
    </div>
    </div>
  </div>
</template>
