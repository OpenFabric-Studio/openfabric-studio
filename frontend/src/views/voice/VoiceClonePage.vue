<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '../../api/http'
import * as voicesApi from '../../api/voices'
import { getActiveVoiceId, isVoiceActive, setActiveVoiceId, voiceErrorText } from '../../api/voices'
import type { VoiceProfile } from '../../api/voices'
import WaveformPlayer from '../../components/shared/WaveformPlayer.vue'
import { createPollingLoop, type PollContext } from '../../composables/polling'
import VoicePreparationReview from './VoicePreparationReview.vue'
import VoiceComparisonLab from './VoiceComparisonLab.vue'
import type { VoicePreparationResponse } from '../../api/contracts'
import { useVoiceSession } from './useVoiceSession'
import { emptyVoiceReviewState, voiceWorkspaceSteps, type VoiceWorkspaceStep } from './voiceWorkspace'
import { voiceStepFacts } from './voiceProgress'
import VoiceJobSummary from './VoiceJobSummary.vue'

const { t } = useI18n()

const voices = ref<VoiceProfile[]>([])
const selectedId = ref<string | null>(null)
const creatingNew = ref(false)
const name = ref('')
const activeId = ref(getActiveVoiceId() ?? '')
const loading = ref(false)
const uploading = ref(false)
const dragging = ref(false)
const error = ref('')
const notice = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const reportOpen = ref(false)
const preparation = ref<VoicePreparationResponse | null>(null)
const reviewSession = ref(0)
const activeStep = ref<VoiceWorkspaceStep>('files')
const reviewState = ref(emptyVoiceReviewState())
const review = ref<InstanceType<typeof VoicePreparationReview> | null>(null)
const stepFacts = computed(() => voiceStepFacts(selected.value, preparation.value, reviewState.value))
function stepState(step: VoiceWorkspaceStep): 'current' | 'complete' | 'available' | 'blocked' {
  return activeStep.value === step ? 'current' : stepFacts.value[step].complete ? 'complete' : stepFacts.value[step].available ? 'available' : 'blocked'
}
function cancelJob(kind: 'preparation' | 'coverage' | 'build') {
  if (kind === 'build') void review.value?.cancelBuild()
  else void review.value?.cancelPreparation()
}
function retryJob(kind: 'preparation' | 'coverage' | 'build') {
  if (kind === 'build') void review.value?.build()
  else if (kind === 'coverage') void review.value?.analyzeCoverage()
  else void review.value?.prepare()
}
const stepIndex = computed(() => voiceWorkspaceSteps.indexOf(activeStep.value))
const nextStep = computed(() => voiceWorkspaceSteps[stepIndex.value + 1])
const nextEnabled = computed(() => {
  if (!nextStep.value || !selected.value) return false
  if (activeStep.value === 'files') return stepFacts.value.files.complete
  if (activeStep.value === 'samples') return stepFacts.value.samples.complete
  if (activeStep.value === 'coverage') return stepFacts.value.build.available
  if (activeStep.value === 'build') return stepFacts.value.compare.available
  return false
})
function navigate(step: VoiceWorkspaceStep, focus = false) {
  activeStep.value = step
  if (focus) document.getElementById(`voice-tab-${step}`)?.focus()
}
function onTabKey(event: KeyboardEvent) {
  let index = stepIndex.value
  if (event.key === 'ArrowRight') index = (index + 1) % voiceWorkspaceSteps.length
  else if (event.key === 'ArrowLeft') index = (index + voiceWorkspaceSteps.length - 1) % voiceWorkspaceSteps.length
  else if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = voiceWorkspaceSteps.length - 1
  else return
  event.preventDefault()
  const step = voiceWorkspaceSteps[index]
  if (step) navigate(step, true)
}

const selected = computed(() => {
  if (creatingNew.value) return null
  return voices.value.find((voice) => voice.id === selectedId.value) ?? null
})
const captureSession = useVoiceSession(() => creatingNew.value ? '__new__' : selectedId.value ?? '')
const buildRunning = computed(() => isVoiceActive(selected.value?.status))
const preparing = computed(() => preparation.value?.status === 'queued' || preparation.value?.status === 'running')
const recordingsChanged = computed(() => {
  const voice = selected.value
  if (!voice || voice.status !== 'ready') return false
  return voice.recordings.map((item) => item.filename).join('\n') !== (voice.built_from || []).join('\n')
})
watch(selected, (voice, previous) => {
  if (voice?.id === previous?.id) return
  preparation.value = null
  activeStep.value = 'files'
  reviewState.value = emptyVoiceReviewState()
  uploading.value = false
  if (!voice) return
  reportOpen.value = false
}, { flush: 'sync' })
const previewSrc = computed(() => {
  const voice = selected.value
  if (!voice?.has_preview) return ''
  return `/api/voices/${voice.id}/preview?v=${voice.status}-${voice.progress_current}-${voice.recordings.length}`
})
const needsPrepareAgain = computed(() => {
  const voice = selected.value
  return Boolean(voice?.usable && !voice.prepared && !buildRunning.value)
})
const reportLines = computed(() => {
  const voice = selected.value
  if (!voice) return []
  const report = voice.extract_report
  const songs = report?.songs || voice.built_from?.length || voice.recordings.length
  if (!songs && !report) return []
  const lines: string[] = []
  if (report) {
    lines.push(report.skipped
      ? t('voiceClone.reportSongsSkipped', { count: songs, skipped: report.skipped })
      : t('voiceClone.reportSongs', { count: songs }))
    lines.push(t(report.cleaned ? 'voiceClone.reportCleanOn' : 'voiceClone.reportCleanOff'))
    if (report.gaps_shortened && report.max_gap_sec >= 0.05) {
      lines.push(t('voiceClone.reportGaps', { gap: formatGap(report.max_gap_sec) }))
    } else if (report.gaps_shortened) {
      lines.push(t('voiceClone.reportGapsBasic'))
    }
    lines.push(t('voiceClone.reportKept', {
      kept: prepMinutes(report.kept_sec),
      total: prepMinutes(report.extracted_sec),
    }))
    if (report.trimmed > 0) lines.push(t('voiceClone.reportTrimmed', { count: report.trimmed }))
    if (report.dropped > 0) lines.push(t('voiceClone.reportDropped', { count: report.dropped }))
    const qualityKey = report.quality === 'clear'
      ? 'voiceClone.reportQualityClear'
      : report.quality === 'weak'
        ? 'voiceClone.reportQualityWeak'
        : 'voiceClone.reportQualityUsable'
    lines.push(t(qualityKey, { level: Number(report.level_db).toFixed(1) }))
    if (report.notes?.includes('quiet')) lines.push(t('voiceClone.reportNoteQuiet'))
    if (report.notes?.includes('hot')) lines.push(t('voiceClone.reportNoteHot'))
    if (report.notes?.includes('clipped')) lines.push(t('voiceClone.reportNoteClipped'))
    return lines
  }
  if (!voice.prepared || (voice.prep_total_sec ?? 0) <= 0) return []
  lines.push(t('voiceClone.reportSongs', { count: songs }))
  lines.push(t(voice.clean_vocals ? 'voiceClone.reportCleanOn' : 'voiceClone.reportCleanOff'))
  lines.push(t('voiceClone.reportGapsBasic'))
  lines.push(t('voiceClone.reportKept', {
    kept: prepMinutes(voice.prep_kept_sec ?? 0),
    total: prepMinutes(voice.prep_total_sec ?? 0),
  }))
  return lines
})

function prepMinutes(seconds: number): string {
  const minutes = Math.max(0, Number(seconds) || 0) / 60
  if (minutes < 10) return (Math.round(minutes * 10) / 10).toFixed(1)
  return String(Math.round(minutes))
}

function formatGap(seconds: number): string {
  const value = Math.max(0, Number(seconds) || 0)
  if (value >= 10) return String(Math.round(value))
  return (Math.round(value * 10) / 10).toFixed(1)
}

function statusLabel(voice: VoiceProfile): string {
  if (voice.status === 'ready') return t('voiceClone.statusReady')
  if (isVoiceActive(voice.status)) return t('voiceClone.statusBuilding')
  if (voice.status === 'failed') return t('voiceClone.statusFailed')
  if (voice.status === 'cancelled') return t('voiceClone.statusCancelled')
  return t('voiceClone.recordingCount', { count: voice.recordings.length })
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return voiceErrorText(err.message)
  return voiceErrorText('unknown')
}

function syncActive() {
  activeId.value = getActiveVoiceId() ?? ''
}

function replaceVoice(voice: VoiceProfile) {
  const index = voices.value.findIndex((item) => item.id === voice.id)
  if (index >= 0) voices.value[index] = voice
  else voices.value = [voice, ...voices.value]
}

let alive = true
let initialLoad = true
const poll = createPollingLoop(async (context) => {
  const showSpinner = initialLoad
  initialLoad = false
  await loadVoices(showSpinner, context)
  return voices.value.some((voice) => isVoiceActive(voice.status))
}, 2000)
function schedule() {
  if (!alive) return
  if (voices.value.some((voice) => isVoiceActive(voice.status))) {
    poll.start(false)
  } else poll.stop()
}

async function loadVoices(showSpinner: boolean, context: PollContext) {
  if (showSpinner) loading.value = true
  error.value = ''
  try {
    const response = await voicesApi.listVoices(context.signal)
    if (!context.isCurrent()) return
    voices.value = response
    const stillThere = selectedId.value && voices.value.some((voice) => voice.id === selectedId.value)
    if (selectedId.value && !stillThere) {
      selectedId.value = creatingNew.value ? null : voices.value[0]?.id ?? null
      if (!voices.value.length) creatingNew.value = false
    } else if (!selectedId.value && !creatingNew.value && voices.value.length) {
      selectedId.value = voices.value[0].id
    }
  } catch (err) {
    if (!context.isCurrent()) return
    error.value = errorText(err)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}

function startNew() {
  creatingNew.value = true
  selectedId.value = null
  name.value = ''
  notice.value = ''
  error.value = ''
}

function selectVoice(id: string) {
  creatingNew.value = false
  selectedId.value = id
  notice.value = ''
  error.value = ''
}

function pickFiles() {
  if (buildRunning.value || preparing.value || uploading.value) return
  fileInput.value?.click()
}

function onDropKey(event: KeyboardEvent) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  pickFiles()
}

async function addFiles(fileList: FileList | File[] | null) {
  if (!fileList || uploading.value || buildRunning.value || preparing.value) return
  const files = Array.from(fileList)
  if (!files.length) return
  let session = captureSession()
  poll.stop()
  uploading.value = true
  error.value = ''
  notice.value = ''
  try {
    let voice = selected.value
    if (!voice) {
      const created = await voicesApi.createVoice(name.value.trim() || t('voiceClone.defaultName'), session.signal)
      if (!session.isCurrent()) return
      name.value = ''
      replaceVoice(created)
      creatingNew.value = false
      selectedId.value = created.id
      session = captureSession()
      uploading.value = true
      voice = created
    }
    const result = await voicesApi.uploadRecordings(voice.id, files, session.signal)
    if (!session.isCurrent()) return
    replaceVoice(result.voice)
    preparation.value = null
    reviewSession.value++
    const skipped = result.skipped.length
      ? t('voiceClone.uploadedSkipped', { count: result.skipped.length })
      : ''
    notice.value = t('voiceClone.uploaded', { count: result.saved.length, skipped })
  } catch (err) {
    if (session.isCurrent()) error.value = errorText(err)
  } finally {
    if (session.isCurrent()) {
      uploading.value = false
      if (fileInput.value) fileInput.value.value = ''
      schedule()
    }
  }
}

function onFilesPicked(event: Event) {
  if (event.target instanceof HTMLInputElement) void addFiles(event.target.files)
}

function onDrop(event: DragEvent) {
  dragging.value = false
  event.preventDefault()
  void addFiles(event.dataTransfer?.files ?? null)
}

async function onDelete(voice: VoiceProfile) {
  const session = captureSession()
  poll.stop()
  error.value = ''
  notice.value = ''
  try {
    await voicesApi.deleteVoice(voice.id, session.signal)
    if (!session.isCurrent()) return
    if (getActiveVoiceId() === voice.id) setActiveVoiceId(null)
    syncActive()
    voices.value = voices.value.filter((item) => item.id !== voice.id)
    if (selectedId.value === voice.id) {
      selectedId.value = voices.value[0]?.id ?? null
      creatingNew.value = false
    }
    schedule()
  } catch (err) {
    if (session.isCurrent()) error.value = errorText(err)
  } finally {
    if (session.isCurrent()) schedule()
  }
}

function onProfile(voice: VoiceProfile) {
  if (!alive || voice.id !== selected.value?.id) return
  replaceVoice(voice)
  poll.stop()
  schedule()
}

function useForSongs() {
  if (!selected.value?.usable) return
  setActiveVoiceId(selected.value.id)
  syncActive()
}

function stopUsing() {
  setActiveVoiceId(null)
  syncActive()
}

onMounted(() => {
  poll.start()
  window.addEventListener('remiqora-voice', syncActive)
  window.addEventListener('storage', syncActive)
})
onBeforeUnmount(() => {
  alive = false
  poll.stop()
  window.removeEventListener('remiqora-voice', syncActive)
  window.removeEventListener('storage', syncActive)
})
</script>

<template>
  <div class="mx-auto max-w-5xl space-y-6">
    <div>
      <h1 class="text-2xl font-semibold text-text">{{ t('voiceClone.title') }}</h1>
      <p class="mt-2 text-sm text-text-dim">{{ t('voiceClone.intro') }}</p>
    </div>

    <p v-if="error" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-3 py-2 text-sm text-status-failed">
      {{ error }}
    </p>
    <p v-if="notice" class="text-sm text-status-done">{{ notice }}</p>
    <p v-if="loading" class="text-sm text-text-dim">{{ t('common.loading') }}</p>

    <ul v-if="voices.length" class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      <li>
        <button
          type="button"
          class="flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors"
          :class="!selected ? 'border-accent1/60 bg-panel-2 text-text' : 'border-border text-text-dim hover:text-text'"
          @click="startNew"
        >
          <span class="font-medium">{{ t('voiceClone.newVoice') }}</span>
        </button>
      </li>
      <li v-for="voice in voices" :key="voice.id">
        <button
          type="button"
          class="flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors"
          :class="voice.id === selected?.id ? 'border-accent1/60 bg-panel-2 text-text' : 'border-border text-text-dim hover:text-text'"
          @click="selectVoice(voice.id)"
        >
          <span class="min-w-0 truncate font-medium">{{ voice.name }}</span>
          <span class="flex shrink-0 items-center gap-2 text-xs">
            <span v-if="voice.id === activeId" class="text-status-done">{{ t('voiceClone.inUseShort') }}</span>
            <span>{{ statusLabel(voice) }}</span>
          </span>
        </button>
      </li>
    </ul>

    <section class="space-y-4 rounded-xl border border-border bg-panel p-5">
      <div v-if="selected" class="flex items-center justify-between gap-3">
        <h2 class="text-lg font-semibold text-text">{{ selected.name }}</h2>
        <button type="button" class="text-xs text-status-failed hover:underline" @click="onDelete(selected)">
          {{ t('voiceClone.deleteVoice') }}
        </button>
      </div>
      <label v-else class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('voiceClone.nameLabel') }}</span>
        <input
          v-model="name"
          type="text"
          maxlength="80"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
          :placeholder="t('voiceClone.namePlaceholder')"
        />
        <span class="block text-xs text-text-dim">{{ t('voiceClone.nameOptional') }}</span>
      </label>

      <div v-if="selected?.usable" class="flex flex-col gap-3 rounded-lg bg-panel-2 p-3 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm" :class="activeId === selected.id ? 'text-status-done' : 'text-text'">
          {{ activeId === selected.id ? t('voiceClone.usingThis') : t('voiceClone.ready') }}
        </p>
        <button
          v-if="activeId === selected.id"
          type="button"
          class="shrink-0 text-sm text-text-dim hover:text-text"
          @click="stopUsing"
        >
          {{ t('voiceClone.stopUsing') }}
        </button>
        <button
          v-else
          type="button"
          class="shrink-0 rounded-lg bg-accent1 px-4 py-2 text-sm font-medium text-white"
          @click="useForSongs"
        >
          {{ t('voiceClone.useThis') }}
        </button>
      </div>

      <div role="tablist" :aria-label="t('voiceClone.workspace.navigation')" class="flex gap-2 overflow-x-auto border-b border-border pb-3 sm:grid sm:grid-cols-5">
        <button v-for="(step, index) in voiceWorkspaceSteps" :id="`voice-tab-${step}`" :key="step" type="button" role="tab" :aria-label="t(`voiceClone.workspace.tab.${step}`)" :aria-selected="activeStep === step" :aria-current="activeStep === step ? 'step' : undefined" :aria-describedby="`voice-objective-${step}`" aria-controls="voice-workspace-panel" :data-step-state="stepState(step)" :tabindex="activeStep === step ? 0 : -1" class="min-h-20 w-40 shrink-0 rounded-lg border px-3 py-3 text-left text-sm transition-colors sm:w-auto" :class="activeStep === step ? 'border-accent1 bg-accent1/10 text-text' : 'border-border text-text-dim hover:text-text'" @click="navigate(step)" @keydown="onTabKey">
          <span class="flex items-center justify-between gap-2 font-medium"><span>{{ index + 1 }}. {{ t(`voiceClone.workspace.tab.${step}`) }}</span><span v-if="stepFacts[step].complete" aria-hidden="true" class="text-status-done">✓</span></span>
          <span :id="`voice-objective-${step}`" class="mt-1 block text-xs">{{ t(`voiceClone.workspace.objective.${step}`) }}</span>
          <span class="mt-2 block text-xs" :class="stepFacts[step].complete ? 'text-status-done' : ''">{{ t(`voiceClone.workspace.state.${stepState(step)}`) }}<template v-if="activeStep === step && stepFacts[step].complete"> · {{ t('voiceClone.workspace.state.complete') }}</template><template v-if="!stepFacts[step].available && stepFacts[step].prerequisite"> · {{ t('voiceClone.workspace.requires', { step: t(`voiceClone.workspace.tab.${stepFacts[step].prerequisite}`) }) }}</template></span>
        </button>
      </div>
      <VoiceJobSummary :voice="selected" :preparation="preparation" :review="reviewState" :uploading="uploading" @cancel="cancelJob" @retry="retryJob" />
      <div class="flex flex-wrap items-center justify-between gap-3 text-sm"><p class="text-text-dim">{{ t('voiceClone.workspace.step', { current: stepIndex + 1, total: voiceWorkspaceSteps.length }) }} · {{ t(`voiceClone.workspace.hint.${activeStep}`) }}</p><button v-if="nextStep" type="button" :disabled="!nextEnabled" class="rounded-lg border border-border px-3 py-2 text-text disabled:opacity-50" @click="navigate(nextStep, true)">{{ t('voiceClone.workspace.next', { step: t(`voiceClone.workspace.tab.${nextStep}`) }) }}</button></div>
      <p v-if="selected && (!stepFacts[activeStep].available || !nextEnabled && nextStep)" class="text-sm text-text-dim">{{ t(`voiceClone.workspace.prerequisite.${activeStep}`) }}</p>
      <div id="voice-workspace-panel" role="tabpanel" :aria-labelledby="`voice-tab-${activeStep}`" tabindex="0" class="space-y-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent1">
      <div
        v-show="activeStep === 'files'"
        role="button"
        :tabindex="buildRunning || preparing || uploading ? -1 : 0"
        :aria-disabled="buildRunning || preparing || uploading"
        class="flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed text-center transition-colors"
        :class="[
          selected?.recordings.length ? 'p-5' : 'p-10',
          buildRunning || preparing || uploading ? 'cursor-default opacity-60' : 'cursor-pointer',
          dragging ? 'border-accent1 bg-accent1/5' : 'border-border',
        ]"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop="onDrop"
        @click="pickFiles"
        @keydown="onDropKey"
      >
        <input
          ref="fileInput"
          type="file"
          multiple
          accept="audio/*,.wav,.mp3,.flac,.ogg,.opus,.m4a"
          class="hidden"
          @click.stop
          @change="onFilesPicked"
        />
        <p class="text-sm text-text">{{ uploading ? t('common.loading') : t('voiceClone.dropHint') }}</p>
        <p class="text-xs text-text-dim">{{ t('voiceClone.dropSub') }}</p>
      </div>
      <p v-if="!selected" class="text-sm text-text-dim">{{ t('voiceClone.dropFirst') }}</p>

      <template v-if="selected">
        <p v-if="activeStep === 'files'" class="text-sm text-text-dim">{{ t('voiceClone.recordingsHint') }}</p>
        <p v-if="activeStep === 'files' && !selected.recordings.length" class="text-sm text-text-dim">{{ t('voiceClone.noRecordings') }}</p>

        <div class="space-y-4 border-t border-border pt-4">
          <p v-if="activeStep === 'files' && selected.status === 'idle' && selected.recordings.length" class="text-sm text-text-dim">
            {{ t('voiceClone.readyToBuild') }}
          </p>
          <VoicePreparationReview ref="review" :key="`${selected.id}-${reviewSession}`" :voice="selected" :step="activeStep" :disabled="uploading" @profile="onProfile" @preparation="preparation = $event" @state="reviewState = $event" @navigate="navigate" />
          <VoiceComparisonLab :key="`comparison-${selected.id}`" :voice="selected" :preparation="preparation" :disabled="uploading" :active="activeStep === 'compare'" :selection-dirty="reviewState.selectionDirty || reviewState.optionsDirty" />
          <div v-if="activeStep === 'build'" class="space-y-3">
          <p v-if="recordingsChanged" class="text-sm text-text-dim">{{ t('voiceClone.recordingsChanged') }}</p>
          <p v-if="needsPrepareAgain" class="text-sm text-text-dim">{{ t('voiceClone.prepareAgain') }}</p>
          <div v-if="reportLines.length" class="rounded-lg bg-panel-2 px-3 py-3">
            <div class="flex items-center justify-between gap-3">
              <p class="text-sm font-medium text-text">{{ t('voiceClone.reportTitle') }}</p>
              <button
                type="button"
                class="text-xs text-text-dim hover:text-text"
                @click="reportOpen = !reportOpen"
              >
                {{ reportOpen ? t('voiceClone.hideReport') : t('voiceClone.showReport') }}
              </button>
            </div>
            <ul v-if="reportOpen" class="mt-2 space-y-1">
              <li v-for="(line, index) in reportLines" :key="index" class="text-sm text-text-dim">{{ line }}</li>
            </ul>
          </div>
          <div v-if="previewSrc" class="space-y-1">
            <p class="text-xs text-text-dim">{{ t('voiceClone.preview') }}</p>
            <WaveformPlayer :src="previewSrc" />
          </div>
          </div>
        </div>
      </template>
      </div>
    </section>
  </div>
</template>
