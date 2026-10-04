<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDialogA11y } from '../../composables/useDialogA11y'
import VoiceOverview from './VoiceOverview.vue'
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
import LocalEnginePanel from './LocalEnginePanel.vue'
import { singingStage, singingInitialStage, publishedSourcesChanged, type SingingStage } from './singingNavigation'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })
const emit = defineEmits<{ activity: [message: string] }>()
const route = useRoute()
const router = useRouter()
// Async singing responses retain their owning bookmark while another workspace
// is visible. Only deliberate navigation in Singing can replace this intent.
let singingIntent = { voice: route.query.voice, stage: singingStage(route.query.stage) }

const { t } = useI18n()

const voices = ref<VoiceProfile[]>([])
const selectedId = ref<string | null>(null)
const creatingNew = ref(false)
const name = ref('')
const search = ref('')
const workspace = ref<HTMLElement | null>(null)
const comparisonActivity = ref('')
const deleting = ref(false)
const deleteTarget = ref<VoiceProfile | null>(null)
const deleteDialog = ref<HTMLElement | null>(null)
useDialogA11y(deleteDialog, () => !!deleteTarget.value, () => { if (!deleting.value) deleteTarget.value = null })
watch(() => props.active, async active => {
  if (active) return
  const focused = document.activeElement
  const outside = focused instanceof HTMLElement && !workspace.value?.contains(focused) ? focused : null
  deleteTarget.value = null
  for (const audio of workspace.value?.querySelectorAll('audio') ?? []) audio.pause()
  await nextTick()
  if (alive && !props.active && outside?.isConnected) outside.focus()
})
const modelChanging = ref(false)
const filteredVoices = computed(() => voices.value.filter(voice => voice.name.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())))
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
const activeStep = ref<SingingStage>('files')
let deliberateNavigation = false
let preparationEntryPending = true
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
const tabs = computed<SingingStage[]>(() => selected.value?.usable ? ['overview', ...voiceWorkspaceSteps] : [...voiceWorkspaceSteps])
const reviewStep = computed<VoiceWorkspaceStep>(() => activeStep.value === 'overview' ? 'compare' : activeStep.value)
function tabLabel(step: SingingStage) { return t(`singingWorkspace.stages.${step}`) }
function writeNavigation(push = true) {
  if (!props.active || route.path !== '/voice-clone' || route.query.mode === 'speech' || route.query.mode === 'audiobooks') return
  const query = { ...route.query, voice: creatingNew.value ? 'new' : selectedId.value ?? undefined, stage: activeStep.value }
  if (route.query.voice === query.voice && route.query.stage === query.stage) return
  void (push ? router.push({ query }) : router.replace({ query }))
}
const stepIndex = computed(() => voiceWorkspaceSteps.indexOf(reviewStep.value))
const nextStep = computed(() => voiceWorkspaceSteps[stepIndex.value + 1])
const nextEnabled = computed(() => {
  if (!nextStep.value || !selected.value) return false
  if (activeStep.value === 'files') return stepFacts.value.files.complete
  if (activeStep.value === 'samples') return stepFacts.value.samples.complete
  if (activeStep.value === 'coverage') return stepFacts.value.build.available
  if (activeStep.value === 'build') return stepFacts.value.compare.available
  return false
})
function navigate(step: SingingStage, focus = false) {
  deliberateNavigation = true
  activeStep.value = step
  writeNavigation()
  if (focus) document.getElementById(`voice-tab-${step}`)?.focus()
}
function onTabKey(event: KeyboardEvent) {
  let index = tabs.value.indexOf(activeStep.value)
  if (event.key === 'ArrowRight') index = (index + 1) % tabs.value.length
  else if (event.key === 'ArrowLeft') index = (index + tabs.value.length - 1) % tabs.value.length
  else if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = tabs.value.length - 1
  else return
  event.preventDefault()
  const step = tabs.value[index]
  if (step) navigate(step, true)
}

const selected = computed(() => {
  if (creatingNew.value) return null
  return voices.value.find((voice) => voice.id === selectedId.value) ?? null
})
const captureSession = useVoiceSession(() => creatingNew.value ? '__new__' : selectedId.value ?? '')
const buildRunning = computed(() => isVoiceActive(selected.value?.status))
const preparing = computed(() => preparation.value?.status === 'queued' || preparation.value?.status === 'running')
const recordingsChanged = computed(() => publishedSourcesChanged(selected.value, preparation.value))
watch(selected, (voice, previous) => {
  if (voice?.id === previous?.id) return
  preparation.value = null
  const requested = !singingIntent.voice || singingIntent.voice === voice?.id ? singingIntent.stage : null
  deliberateNavigation = requested !== null
  preparationEntryPending = true
  activeStep.value = requested ?? singingInitialStage(voice)
  if (!voice || activeStep.value === 'overview' && !voice.usable) activeStep.value = 'files'
  deleteTarget.value = null
  deleting.value = false
  modelChanging.value = false
  reviewState.value = emptyVoiceReviewState()
  uploading.value = false
  if (!voice) return
  reportOpen.value = false
}, { flush: 'sync' })
watch(preparation, value => {
  if (!value || !preparationEntryPending) return
  preparationEntryPending = false
  if (!deliberateNavigation && (value.status === 'queued' || value.status === 'running')) {
    activeStep.value = value.operation === 'coverage' ? 'coverage' : 'files'
  }
})
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
let initialSelection = true
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
    if (initialSelection) {
      initialSelection = false
      const requestedVoice = singingIntent.voice
      if (requestedVoice === 'new') { creatingNew.value = true; selectedId.value = null }
      else if (typeof requestedVoice === 'string' && response.some(voice => voice.id === requestedVoice)) selectedId.value = requestedVoice
    }
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

watch(() => [route.path, route.query.voice, route.query.stage, route.query.mode], (_values, previous) => {
  if (!props.active || route.path !== '/voice-clone' || route.query.mode === 'speech' || route.query.mode === 'audiobooks') return
  // A bare sidebar return resumes the retained workspace; deliberate singing
  // bookmarks and browser history within Voice Clone still restore their query.
  if (previous[0] !== '/voice-clone' && route.query.voice === undefined && route.query.stage === undefined) return
  // Switching back from Speech resumes Singing when its bookmark fields did
  // not change. A real voice/stage history change still restores the bookmark.
  if (previous[0] === '/voice-clone' && previous[3] === 'speech' && previous[1] === route.query.voice && previous[2] === route.query.stage) return
  singingIntent = { voice: route.query.voice, stage: singingStage(route.query.stage) }
  if (route.query.voice === 'new') { creatingNew.value = true; selectedId.value = null }
  else if (typeof route.query.voice === 'string' && voices.value.some(voice => voice.id === route.query.voice)) { creatingNew.value = false; selectedId.value = route.query.voice }
  else { creatingNew.value = false; selectedId.value = voices.value[0]?.id ?? null }
  const stage = singingIntent.stage
  deliberateNavigation = stage !== null
  if (stage && (stage !== 'overview' || selected.value?.usable)) activeStep.value = stage
  else activeStep.value = preparation.value?.status === 'running' || preparation.value?.status === 'queued' ? preparation.value.operation === 'coverage' ? 'coverage' : 'files' : singingInitialStage(selected.value)
}, { flush: 'post' })
const activity = computed(() => {
  if (uploading.value) return t('voiceClone.workspace.uploading')
  if (deleting.value || modelChanging.value || reviewState.value.action) return t('singingWorkspace.actionPending')
  if (error.value || reviewState.value.error) return error.value || reviewState.value.error
  if (comparisonActivity.value) return comparisonActivity.value
  const working = voices.value.filter(voice => isVoiceActive(voice.status))
  if (working.length) return t('singingWorkspace.backgroundBuild', { count: working.length })
  if (preparation.value?.status === 'queued' || preparation.value?.status === 'running') return t('voiceClone.workspace.preparing')
  if (preparation.value?.status === 'failed' || preparation.value?.status === 'cancelled') return `${selected.value?.name ?? ''} · ${t(`voiceClone.review.status.${preparation.value.status}`)}`
  if (selected.value?.status === 'failed' || selected.value?.status === 'cancelled') return `${selected.value.name} · ${statusLabel(selected.value)}`
  return ''
})
watch(activity, message => { if (alive) emit('activity', message) }, { immediate: true })
function startNew() {
  creatingNew.value = true
  selectedId.value = null
  notice.value = ''
  error.value = ''
  activeStep.value = 'files'
  writeNavigation()
}

function selectVoice(id: string) {
  creatingNew.value = false
  selectedId.value = id
  notice.value = ''
  error.value = ''
  deliberateNavigation = false
  activeStep.value = preparation.value?.status === 'running' || preparation.value?.status === 'queued' ? preparation.value.operation === 'coverage' ? 'coverage' : 'files' : singingInitialStage(selected.value)
  void router.push({ query: { ...route.query, voice: id, stage: undefined } })
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
      writeNavigation(false)
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

async function confirmDelete() {
  const voice = deleteTarget.value
  if (!voice || deleting.value) return
  deleting.value = true
  const session = captureSession()
  poll.stop()
  error.value = ''
  notice.value = ''
  try {
    await voicesApi.deleteVoice(voice.id, session.signal)
    if (!session.isCurrent()) return
    deleteTarget.value = null
    if (getActiveVoiceId() === voice.id) setActiveVoiceId(null)
    syncActive()
    voices.value = voices.value.filter((item) => item.id !== voice.id)
    if (selectedId.value === voice.id) {
      selectedId.value = voices.value[0]?.id ?? null
      creatingNew.value = false
      writeNavigation(false)
    }
    schedule()
  } catch (err) {
    if (session.isCurrent()) error.value = errorText(err)
  } finally {
    if (session.isCurrent()) { deleting.value = false; schedule() }
  }
}

async function choosePublishedModel(event: Event) {
  if (!(event.target instanceof HTMLSelectElement) || modelChanging.value || uploading.value || buildRunning.value || preparing.value || reviewState.value.action) return
  const modelId = event.target.value
  if (!selected.value?.models?.some(model => model.id === modelId)) return
  const session = captureSession()
  modelChanging.value = true; error.value = ''
  try {
    const response = await voicesApi.selectVoiceModel(session.id, modelId, session.signal)
    if (session.isCurrent()) onProfile(response)
  } catch (err) { if (session.isCurrent()) error.value = errorText(err) }
  finally { if (session.isCurrent()) modelChanging.value = false }
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
  window.addEventListener('openfabric-voice', syncActive)
  window.addEventListener('storage', syncActive)
})
onBeforeUnmount(() => {
  alive = false
  poll.stop()
  window.removeEventListener('openfabric-voice', syncActive)
  window.removeEventListener('storage', syncActive)
})
</script>

<template>
  <div ref="workspace" class="grid items-start gap-5 lg:grid-cols-[15rem_minmax(0,1fr)]">
    <aside class="space-y-4 rounded-xl border border-border bg-panel p-4" :aria-label="t('singingWorkspace.library')">
      <div class="flex items-center justify-between gap-2"><h2 class="text-sm font-semibold text-text">{{ t('singingWorkspace.library') }}</h2><span class="text-xs text-text-dim">{{ voices.length }}</span></div>
      <button type="button" :aria-label="t('voiceClone.newVoice')" class="min-h-11 w-full rounded-lg bg-accent1 px-3 py-2 text-sm font-medium text-white" @click="startNew">+ {{ t('voiceClone.newVoice') }}</button>
      <label class="block space-y-1 text-xs text-text-dim"><span>{{ t('singingWorkspace.search') }}</span><input v-model="search" type="search" :aria-label="t('singingWorkspace.search')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 text-sm text-text" /></label>
      <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('common.loading') }}</p>
      <ul class="max-h-64 space-y-2 overflow-y-auto lg:max-h-[65vh]">
        <li v-for="voice in filteredVoices" :key="voice.id"><button type="button" :aria-pressed="voice.id === selected?.id" class="min-h-14 w-full rounded-lg border p-3 text-left transition-colors" :class="voice.id === selected?.id ? 'border-accent1/60 bg-accent1/10 text-text' : 'border-border text-text-dim hover:bg-panel-2 hover:text-text'" @click="selectVoice(voice.id)"><span class="block break-words text-sm font-medium">{{ voice.name }}</span><span class="mt-1 block text-xs"><span v-if="voice.id === activeId" class="text-status-done">{{ t('voiceClone.inUseShort') }} · </span>{{ statusLabel(voice) }}</span></button></li>
      </ul>
      <p v-if="!loading && !filteredVoices.length" class="text-sm text-text-dim">{{ t(voices.length ? 'voiceClone.workspace.noMatches' : 'singingWorkspace.empty') }}</p>
    </aside>
    <div class="min-w-0 space-y-4">
      <p v-if="error" role="alert" class="rounded-lg border border-status-failed/40 bg-status-failed/10 px-3 py-2 text-sm text-status-failed">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm text-status-done">{{ notice }}</p>
    <section class="space-y-4 rounded-xl border border-border bg-panel p-5">
      <div v-if="selected" class="flex items-center justify-between gap-3">
        <h2 data-singing-heading class="min-w-0 flex-1 break-words text-lg font-semibold text-text">{{ selected.name }}</h2>
        <button type="button" class="min-h-10 rounded-lg border border-border px-3 py-2 text-xs text-text-dim hover:text-status-failed disabled:opacity-50" :disabled="buildRunning || preparing || uploading || deleting" @click="deleteTarget = selected">
          {{ t('voiceClone.deleteVoice') }}
        </button>
      </div>
      <label v-else class="block space-y-1">
        <span class="text-xs text-text-dim">{{ t('voiceClone.nameLabel') }}</span>
        <input
          data-singing-name
          v-model="name"
          type="text"
          maxlength="80"
          class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
          :placeholder="t('voiceClone.namePlaceholder')"
        />
        <span class="block text-xs text-text-dim">{{ t('voiceClone.nameOptional') }}</span>
      </label>

      <div role="tablist" :aria-label="t('voiceClone.workspace.navigation')" class="flex gap-1 overflow-x-auto border-b border-border pb-2">
        <button v-for="step in tabs" :id="`voice-tab-${step}`" :key="step" type="button" role="tab" :aria-label="tabLabel(step)" :aria-selected="activeStep === step" aria-controls="voice-workspace-panel" :data-step-state="step === 'overview' ? undefined : stepState(step)" :tabindex="activeStep === step ? 0 : -1" class="min-h-11 shrink-0 rounded-lg px-3 py-2 text-sm transition-colors" :class="activeStep === step ? 'bg-accent1/15 text-text ring-1 ring-accent1/50' : 'text-text-dim hover:bg-panel-2 hover:text-text'" @click="navigate(step)" @keydown="onTabKey"><span v-if="step !== 'overview'" class="mr-1 text-text-dim">{{ voiceWorkspaceSteps.indexOf(step) + 1 }}.</span>{{ tabLabel(step) }}<span v-if="step !== 'overview' && stepFacts[step].complete" aria-hidden="true" class="ml-2 text-status-done">✓</span></button>
      </div>
      <VoiceJobSummary :voice="selected" :preparation="preparation" :review="reviewState" :uploading="uploading" @cancel="cancelJob" @retry="retryJob" />
      <div v-if="activeStep !== 'overview'" class="flex flex-wrap items-center justify-between gap-3 text-sm"><p class="text-text-dim">{{ t('voiceClone.workspace.step', { current: stepIndex + 1, total: voiceWorkspaceSteps.length }) }} · {{ t(`voiceClone.workspace.hint.${activeStep}`) }}</p><button v-if="nextStep" type="button" :disabled="!nextEnabled" class="rounded-lg border border-border px-3 py-2 text-text disabled:opacity-50" @click="navigate(nextStep, true)">{{ t('voiceClone.workspace.next', { step: tabLabel(nextStep) }) }}</button></div>
      <p v-if="selected && activeStep !== 'overview' && (!stepFacts[activeStep].available || !nextEnabled && nextStep)" class="text-sm text-text-dim">{{ t(`voiceClone.workspace.prerequisite.${activeStep}`) }}</p>
      <div id="voice-workspace-panel" role="tabpanel" :aria-labelledby="`voice-tab-${activeStep}`" tabindex="0" class="space-y-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent1">
      <VoiceOverview v-if="selected && activeStep === 'overview'" :voice="selected" :active="activeId === selected.id" :busy="modelChanging || uploading || buildRunning || preparing || !!reviewState.action" @model="choosePublishedModel" @compare="navigate('compare')" @use="useForSongs" @stop="stopUsing" @prepare="navigate('files')" />
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

        <div :class="activeStep === 'overview' ? '' : 'space-y-4 border-t border-border pt-4'">
          <p v-if="activeStep === 'files' && selected.status === 'idle' && selected.recordings.length" class="text-sm text-text-dim">
            {{ t('voiceClone.readyToBuild') }}
          </p>
          <VoicePreparationReview ref="review" :key="`${selected.id}-${reviewSession}`" :voice="selected" :step="reviewStep" v-show="activeStep !== 'overview'" :disabled="uploading" @profile="onProfile" @preparation="preparation = $event" @state="reviewState = $event" @navigate="navigate" />
          <VoiceComparisonLab :key="`comparison-${selected.id}`" :voice="selected" :preparation="preparation" :disabled="uploading" :active="activeStep === 'compare'" :selection-dirty="reviewState.selectionDirty || reviewState.optionsDirty" @activity="comparisonActivity = $event" />
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
    <LocalEnginePanel class="lg:col-span-2" kind="singing" :active="active" />
    </div>
    <Teleport to="body"><div v-if="deleteTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"><section ref="deleteDialog" data-voice-confirmation role="dialog" aria-modal="true" aria-labelledby="singing-delete-title" aria-describedby="singing-delete-body" tabindex="-1" class="w-full max-w-md space-y-4 rounded-xl border border-border bg-panel p-5"><h2 id="singing-delete-title" class="text-lg font-semibold">{{ t('singingWorkspace.deleteTitle', { name: deleteTarget.name }) }}</h2><p id="singing-delete-body" class="text-sm text-text-dim">{{ t('singingWorkspace.deleteHint') }}</p><div class="flex justify-end gap-3"><button type="button" :disabled="deleting" class="min-h-11 rounded-lg border border-border px-4 text-sm" @click="deleteTarget = null">{{ t('common.cancel') }}</button><button data-confirm-delete type="button" :disabled="deleting" class="min-h-11 rounded-lg bg-status-failed px-4 text-sm text-white disabled:opacity-50" @click="confirmDelete">{{ deleting ? t('common.loading') : t('voiceClone.deleteVoice') }}</button></div></section></div></Teleport>
  </div>
</template>
