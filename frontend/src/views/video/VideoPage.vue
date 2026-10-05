<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useVideoWorkspace } from './useVideoWorkspace'
import SpeechAudioPreview from '../voice/SpeechAudioPreview.vue'
import VideoPreviewPlayer from './VideoPreviewPlayer.vue'
import VideoProjectLibrary from './VideoProjectLibrary.vue'
import VideoDirectionPanel from './VideoDirectionPanel.vue'
import VideoDialoguePanel from './VideoDialoguePanel.vue'
import VideoSoundtrackPanel from './VideoSoundtrackPanel.vue'
import VideoCharacterPanel from './VideoCharacterPanel.vue'
import VideoCharacterTrainer from './VideoCharacterTrainer.vue'
import LocalEnginePanel from '../voice/LocalEnginePanel.vue'
import { videoWorkspaceSteps, videoClipLengths, videoMediaUrl, changeShotLength, splitShot, duplicateShot, moveShot, newVideoId, frameTime, shotProblem, characterLockIssue, type VideoWorkspaceStep, type VideoClipLength } from './videoWorkspace'
import { videoErrorText, videoRequestError, deleteVideo, isVideoActive } from '../../api/videos'
import type { VideoProjectJob, VideoMarker } from '../../api/contracts'
import { formatClock } from '../../composables/voicePace'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import { notePhasePace, phaseRemaining, type VideoPhasePace } from './videoJobTiming'

const { t } = useI18n()
const route = useRoute()
const { tracks, projects, legacyVideos, project, draft, step, selectedShotId, selectedPreviewIds, variantsPerShot, trackId,
  selectedTrack, selectedShot, savedShot, loading, acting, saving, dirty, error, saveError, serverBusy, readiness, now, undoStack, active, readOnly, problem, coverageEnd, approvalCount,
  save, selectProject, removeProject, reloadProject, createProject, preview, render, analyze, approve, resume, cancel, duplicate, exportVideo, upload, uploadSpeech, clearSpeech, speakLine, applyCharacter, applyTrainedCharacter, editShots, undo, redo, refreshCue, addShot } = useVideoWorkspace()
watch([() => route.query.project, loading], ([id, busy]) => {
  if (!busy && typeof id === 'string' && /^[0-9a-f]{32}$/.test(id)) void selectProject(id)
}, { immediate: true })
const exportSource = computed(() => project.value?.file_url ? videoMediaUrl(project.value.file_url, project.value.output_version) : '')
const exportPoster = computed(() => project.value?.poster_url ? videoMediaUrl(project.value.poster_url, project.value.output_version) : '')
const ripple = ref(true)
const audio = ref<HTMLAudioElement | null>(null)
const position = ref(0)
const auditionEnd = ref<number | null>(null)
const audioError = ref('')
let playbackGeneration = 0
const search = ref('')
const statusFilter = ref('all')
const page = ref(1)
const phasePace = ref<VideoPhasePace>()
watch(() => project.value?.job, (job) => { phasePace.value = job ? notePhasePace(phasePace.value, job, Date.now()) : undefined }, { deep: true })
const phaseEta = computed(() => project.value?.job ? phaseRemaining(phasePace.value, project.value.job, now.value) : null)
const songSearch = ref('')
const creatingProject = ref(false)
const showNewProject = computed(() => creatingProject.value || !project.value)
const desktopLibrary = ref(window.innerWidth >= 1280)
const libraryOpen = ref(desktopLibrary.value)
const panelHeading = ref<HTMLElement | null>(null)
let returnStep: VideoWorkspaceStep = 'song'
let alive = true, focusGeneration = 0
function resizeLibrary() {
  const desktop = window.innerWidth >= 1280
  if (desktop !== desktopLibrary.value) { desktopLibrary.value = desktop; libraryOpen.value = desktop }
}
function libraryToggled(event: Event) {
  if (event.target instanceof HTMLDetailsElement) libraryOpen.value = event.target.open
}
onMounted(() => window.addEventListener('resize', resizeLibrary))
onBeforeUnmount(() => { alive = false; focusGeneration++; window.removeEventListener('resize', resizeLibrary) })
const searchedTracks = computed(() => tracks.value.filter((track) => `${track.title} ${track.filename} ${track.short_id ?? ''}`.toLowerCase().includes(songSearch.value.toLowerCase())))
const engineOption = computed(() => readiness.value?.options.find((option) => option.id === (draft.value?.settings.engine_pack ?? 'ltx23')))
const generatedMode = computed(() => draft.value?.mode === 'generated')
const repairDescriptionFor = ref('')
watch([() => selectedShot.value?.id, generatedMode, () => selectedShot.value ? shotProblem(draft.value?.shots ?? [], selectedShot.value.id, timelineLimit.value) === 'bad_prompt' : false], ([id, generated, invalid]) => {
  if (generated || id !== repairDescriptionFor.value) repairDescriptionFor.value = ''
  // Keep the recovery editor mounted while typing; valid image descriptions
  // stay hidden until a stored value actually needs correction.
  if (id && !generated && invalid) repairDescriptionFor.value = id
}, { immediate: true })
const modeReady = computed(() => Boolean(readiness.value?.ffmpeg_ready) && (!generatedMode.value || Boolean(engineOption.value?.available)))
const canAnalyze = computed(() => !readOnly.value && !serverBusy.value && Boolean(readiness.value?.analysis_ready && readiness.value.ffmpeg_ready) && !project.value?.source_changed && project.value?.track_id != null)
const pictureProject = computed(() => project.value?.track_id == null)
const reelProject = computed(() => project.value?.preset === 'reel')
const characterIssue = computed(() => draft.value && project.value ? characterLockIssue({ ...project.value, character_lock: draft.value.character_lock }, draft.value.shots) : '')
const shotCap = computed(() => reelProject.value ? 4 : 40)
const clipLengths = computed((): readonly VideoClipLength[] => reelProject.value ? [2, 4, 6] : videoClipLengths)
const timelineLimit = computed(() => reelProject.value ? 15 : (project.value?.duration_sec ?? 0))
const startKind = ref<'song' | 'silent' | 'reel'>('song')
const silentSeconds = ref(12)
const reelSeconds = ref(12)
const elapsed = computed(() => {
  const job = project.value?.job
  if (!job?.started_at) return null
  const start = Date.parse(job.started_at)
  const finish = job.finished_at ? Date.parse(job.finished_at) : now.value
  return Number.isFinite(start) && Number.isFinite(finish) ? Math.max(0, (finish - start) / 1000) : null
})
const clockText = (seconds: number) => formatClock(seconds)
const statusText = computed(() => project.value?.job?.status ?? 'draft')
const validShots = computed(() => Boolean(draft.value?.shots.length) && !problem.value)
const shotsReady = computed(() => validShots.value && modeReady.value && !readOnly.value && !project.value?.source_changed && (generatedMode.value || (project.value?.references?.length ?? 0) > 0))
const canCompute = computed(() => shotsReady.value && (!generatedMode.value || !serverBusy.value))
const textReady = computed(() => !draft.value?.export_settings.include_overlays || !draft.value.overlays.length || Boolean(readiness.value?.overlay_ready))
const canAssemble = computed(() => canCompute.value && textReady.value)
const canExport = computed(() => shotsReady.value && textReady.value)
const canResume = computed(() => project.value?.job?.operation === 'export' ? canExport.value : project.value?.job?.operation === 'preview' ? canCompute.value : canAssemble.value)
function videoBlocker(generation: boolean): string {
  if (readOnly.value) return t('videoExperience.busy')
  if (project.value?.source_changed) return t('videoWorkspace.sourceChanged')
  if (!readiness.value) return t('videoWorkspace.readinessFailed')
  if (!readiness.value.ffmpeg_ready) return t('video.err.ffmpeg_missing')
  if (!draft.value?.shots.length) return t('videoExperience.noShotsBlocker')
  if (problem.value) return videoErrorText(problem.value)
  if (generatedMode.value && !engineOption.value?.available) return t('videoExperience.modelMissing')
  if (!generatedMode.value && !project.value?.references?.length) return t('videoExperience.referencesMissing')
  if (generation && generatedMode.value && serverBusy.value) return t('video.othersBusy')
  return ''
}
const computeBlocker = computed(() => videoBlocker(true))
const assemblyBlocker = computed(() => computeBlocker.value || (!textReady.value ? t('videoWorkspace.textDependencyHint') : ''))
const exportBlocker = computed(() => videoBlocker(false) || (!textReady.value ? t('videoWorkspace.textDependencyHint') : '') || (approvalCount.value !== draft.value?.shots.length ? t('videoExperience.approvalRequired') : ''))
const shotVariants = computed(() => savedShot.value?.variants ?? [])
function shotStrip(id: string): string {
  const shot = project.value?.shots?.find(item => item.id === id)
  const variant = shot?.variants?.find(item => item.id === shot.approved_variant_id && item.status === 'ready') ?? shot?.variants?.find(item => item.status === 'ready')
  return variant?.filmstrip_url || variant?.poster_url || ''
}

const filteredVideos = computed(() => legacyVideos.value.filter((row) => (statusFilter.value === 'all' || row.status === statusFilter.value)
  && `${row.title} ${row.prompt}`.toLowerCase().includes(search.value.toLowerCase())))
const visibleVideos = computed(() => filteredVideos.value.slice((page.value - 1) * 10, page.value * 10))
const waves = computed(() => (project.value?.analysis?.waveform_peaks ?? []).map((value, index, all) => `${index / Math.max(1, all.length - 1) * 1000},${45 - value * 40}`).join(' '))
const energy = computed(() => (project.value?.analysis?.energy ?? []).map((point) => `${point.time_sec / (project.value?.duration_sec || 1) * 1000},${45 - point.value * 40}`).join(' '))
const importantMarkers = computed(() => (draft.value?.markers ?? []).filter((marker) => marker.kind !== 'beat').slice(0, 100))
const gapSeconds = computed(() => {
  let cursor = 0, gaps = 0
  for (const shot of [...(draft.value?.shots ?? [])].sort((a, b) => a.start_sec - b.start_sec)) {
    gaps += Math.max(0, shot.start_sec - cursor)
    cursor = Math.max(cursor, shot.start_sec + (shot.seconds ?? 4))
  }
  return gaps + Math.max(0, (project.value?.duration_sec ?? 0) - cursor)
})
function chooseTrack(event: Event) { if (event.target instanceof HTMLSelectElement) trackId.value = Number(event.target.value) || null }
async function focusPanel(initiating: Element | null = document.activeElement, token = ++focusGeneration) {
  await nextTick()
  if (alive && token === focusGeneration && (document.activeElement === initiating || document.activeElement === document.body)) panelHeading.value?.focus()
}
function changeStep(value: VideoWorkspaceStep, focus = false) {
  if (showNewProject.value && value !== 'song') return
  step.value = value
  if (focus) void focusPanel()
}
function beginPicture() {
  const seed = Math.floor(Math.random() * 2147483648)
  if (startKind.value === 'reel') void createProject({ name: 'Reel', preset: 'reel', duration_sec: reelSeconds.value, seed })
  else void createProject({ name: 'Silent video', duration_sec: silentSeconds.value, seed })
}
function startNewProject() {
  if (acting.value || saving.value || loading.value) return
  if (!creatingProject.value) returnStep = step.value
  creatingProject.value = true
  if (!tracks.value.some(track => track.id === trackId.value)) trackId.value = tracks.value[0]?.id ?? null
  step.value = 'song'
  if (!desktopLibrary.value) libraryOpen.value = false
  void focusPanel()
}
function cancelNewProject() {
  creatingProject.value = false
  trackId.value = project.value?.track_id ?? null
  step.value = returnStep
  void focusPanel()
}
async function openProject(id: string) {
  const token = ++focusGeneration, initiating = document.activeElement
  await selectProject(id)
  if (!alive || project.value?.id !== id) return
  creatingProject.value = false
  if (!desktopLibrary.value) libraryOpen.value = false
  void focusPanel(initiating, token)
}
watch(() => project.value?.id, (id, previous) => { if (id && id !== previous) creatingProject.value = false })
watch(step, (_value, previous) => {
  focusGeneration++
  const focused = document.activeElement
  if (focused && document.getElementById(`video-panel-${previous}`)?.contains(focused)) void focusPanel(focused)
}, { flush: 'sync' })
function stepKey(event: KeyboardEvent, index: number) {
  if (showNewProject.value) return
  const target = event.key === 'Home' ? 0 : event.key === 'End' ? 4 : event.key === 'ArrowRight' ? (index + 1) % 5 : event.key === 'ArrowLeft' ? (index + 4) % 5 : null
  if (target === null) return
  event.preventDefault()
  const next = videoWorkspaceSteps[target]
  if (next) { step.value = next; document.getElementById(`video-step-${next}`)?.focus() }
}
function stepDone(value: VideoWorkspaceStep) {
  if (showNewProject.value || !project.value || !draft.value) return false
  return value === 'song' ? Boolean(project.value) : value === 'direction' ? Boolean(draft.value?.direction || pictureProject.value || draft.value?.mode !== 'generated')
    : value === 'storyboard' ? validShots.value : value === 'preview' ? approvalCount.value > 0 && approvalCount.value === draft.value?.shots.length : Boolean(project.value?.file_url)
}
function length(value: VideoClipLength) {
  if (draft.value && selectedShot.value) editShots(changeShotLength(draft.value.shots, selectedShot.value.id, value, ripple.value))
}
function removeShot() {
  const shot = selectedShot.value
  if (!draft.value || !shot) return
  editShots(draft.value.shots.filter((item) => item.id !== shot.id).map((item) => ripple.value && item.start_sec > shot.start_sec ? { ...item, start_sec: frameTime(item.start_sec - (shot.seconds ?? 4)) } : item))
}
function snapShot(marker: VideoMarker) {
  if (!draft.value || !selectedShot.value) return
  editShots(draft.value.shots.map((shot) => shot.id === selectedShotId.value ? { ...shot, start_sec: frameTime(marker.time_sec) } : shot))
}
async function audition() {
  if (!audio.value || !selectedShot.value) return
  const source = audio.value
  const token = playbackGeneration
  source.currentTime = selectedShot.value.start_sec
  auditionEnd.value = selectedShot.value.start_sec + (selectedShot.value.seconds ?? 4)
  try { await source.play(); if (token === playbackGeneration) audioError.value = '' } catch { if (token === playbackGeneration) audioError.value = t('videoWorkspace.audioFailed') }
}
function audioTime() {
  if (!audio.value) return
  position.value = audio.value.currentTime
  if (auditionEnd.value !== null && position.value >= auditionEnd.value) { audio.value.pause(); auditionEnd.value = null }
}
function audioStarted() { if (audio.value) claimPlayback(audio.value) }
function audioStopped() { if (audio.value) releasePlaybackIfCurrent(audio.value) }
function seek(seconds: number) { if (audio.value) { audio.value.currentTime = seconds; position.value = seconds; auditionEnd.value = null } }
function addMarker() {
  if (draft.value) draft.value.markers.push({ id: newVideoId(), time_sec: frameTime(position.value), kind: 'manual', label: t('videoWorkspace.manualMarker'), confidence: 1 })
}
function addOverlay() {
  if (draft.value && project.value) draft.value.overlays.push({ id: newVideoId(), kind: 'title', text: project.value.track_title || project.value.name,
    start_sec: 0, end_sec: Math.min(4, project.value.duration_sec), position: 'bottom', font_size: 36, color: '#ffffff' })
}
async function removeLegacy(id: string) {
  if (!window.confirm(t('video.confirmDelete'))) return
  try { await deleteVideo(id); legacyVideos.value = legacyVideos.value.filter((row) => row.id !== id) } catch (cause) { error.value = videoRequestError(cause) }
}
function jobDetail(job: VideoProjectJob) { return [job.phase || t(`videoWorkspace.operations.${job.operation}`), job.shot_count ? `${job.shot_index ?? 0} / ${job.shot_count}` : ''].filter(Boolean).join(' · ') }
watch([search, statusFilter], () => { page.value = 1 })
watch(generatedMode, (generated) => { if (!generated && variantsPerShot.value > 2) variantsPerShot.value = 2 })
function stopSource() { playbackGeneration++; if (audio.value) { audio.value.pause(); releasePlaybackIfCurrent(audio.value) } }
watch(showNewProject, (visible) => {
  if (!visible) return
  stopSource()
  position.value = 0
  auditionEnd.value = null
  audioError.value = ''
}, { flush: 'sync' })
watch(() => selectedTrack.value?.audio_url, () => { stopSource(); position.value = 0; auditionEnd.value = null; audioError.value = '' })
onBeforeUnmount(stopSource)
</script>

<template>
  <div class="video-workspace min-w-0 space-y-6">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div><h1 class="text-2xl font-semibold text-text">{{ t('videoWorkspace.title') }}</h1><p class="mt-2 max-w-2xl text-sm leading-relaxed text-text-dim">{{ t('videoExperience.intro') }}</p></div>
    </header>
    <div class="video-project-layout">
      <aside class="min-w-0 space-y-3" :aria-label="t('videoExperience.projects')">
        <button type="button" class="primary w-full" :aria-label="t('videoExperience.newProject')" :disabled="acting || saving || loading" @click="startNewProject">+ {{ t('videoExperience.newProject') }}</button>
        <details :open="libraryOpen" class="video-library-disclosure" @toggle="libraryToggled">
          <summary class="video-library-summary">{{ t('videoExperience.projects') }} <span class="text-text-dim">{{ projects.length }}</span></summary>
          <VideoProjectLibrary :projects="projects" :selected-id="project?.id" :busy="acting || saving" compact @open="openProject" @delete="removeProject" />
        </details>
      </aside>
      <div data-video-workspace-main class="min-w-0 space-y-5">
    <div data-testid="video-global-status" class="video-step-status sticky z-10 space-y-3 rounded-xl border border-border bg-panel p-4 shadow-sm">
      <div class="flex min-w-0 flex-wrap items-center justify-between gap-2"><div class="min-w-0"><p class="text-xs text-text-dim">{{ t('videoExperience.currentProject') }}</p><p class="mt-1 break-words font-semibold text-text">{{ project?.name || t('videoWorkspace.noProject') }}</p></div><span class="text-xs text-text-dim">{{ t('videoExperience.stageCount', { current: videoWorkspaceSteps.indexOf(step) + 1 }) }}</span></div>
      <nav role="tablist" :aria-label="t('videoWorkspace.steps')" class="video-stage-nav">
        <button v-for="(item, index) in videoWorkspaceSteps" :id="`video-step-${item}`" :key="item" role="tab" :aria-label="`${index + 1} ${t(`videoWorkspace.${item}`)}`" :aria-controls="`video-panel-${item}`" :aria-selected="step === item" :tabindex="step === item ? 0 : -1" :disabled="showNewProject && item !== 'song'"
          class="video-stage" @click="changeStep(item)" @keydown="stepKey($event, index)"><span class="video-stage-number" aria-hidden="true">{{ stepDone(item) ? '✓' : index + 1 }}</span><span class="min-w-0 text-left"><span class="block text-sm font-medium">{{ t(`videoWorkspace.${item}`) }}</span><span class="mt-0.5 block text-xs opacity-80">{{ t(`videoExperience.stageHints.${item}`) }}</span></span></button>
      </nav>
      <div class="flex flex-wrap items-center justify-between gap-2 text-sm">
        <div><span role="status">{{ t(`videoWorkspace.status.${statusText}`) }}<span v-if="project?.job"> · {{ jobDetail(project.job) }}</span></span>
          <span v-if="project?.job?.progress_total"> · {{ project.job.progress_current ?? 0 }} / {{ project.job.progress_total }}</span>
          <span v-if="elapsed !== null"> · {{ t('videoWorkspace.spent', { time: clockText(elapsed) }) }}</span>
          <span v-if="active"> · {{ t('videoWorkspace.estimating') }}</span>
          <span v-if="active && phaseEta !== null"> · {{ t('videoWorkspace.phaseRemaining', { time: clockText(phaseEta) }) }}</span>
        </div>
        <div class="flex items-center gap-2"><span v-if="saving">{{ t('videoWorkspace.saving') }}</span><span v-else-if="dirty">{{ t('videoWorkspace.unsaved') }}</span><span v-else-if="project">{{ t('videoWorkspace.saved') }}</span>
          <button v-if="dirty" :disabled="readOnly || saving" @click="save">{{ t('videoWorkspace.save') }}</button><button v-if="active" :disabled="acting" @click="cancel">{{ t('video.cancel') }}</button>
        </div>
      </div>
      <progress v-if="active && project?.job?.progress_total" class="w-full" :value="project.job.progress_current ?? 0" :max="project.job.progress_total" :aria-label="t('videoWorkspace.progress')"></progress>
    </div>
    <p v-if="loading" role="status">{{ t('videoWorkspace.loading') }}</p>
    <p v-if="error" role="alert" class="text-status-failed">{{ videoErrorText(error) }}</p>
    <p v-if="saveError" role="alert" class="text-status-failed">{{ t('videoWorkspace.saveFailed') }} {{ videoErrorText(saveError) }} <button :disabled="acting" @click="reloadProject">{{ t('videoWorkspace.discardReload') }}</button></p>
    <div v-if="project?.source_changed" data-video-source-warning role="alert" class="video-notice"><p>{{ t('videoWorkspace.sourceChanged') }}</p><button type="button" :disabled="acting || saving" @click="startNewProject">{{ t('videoExperience.createRecovery') }}</button></div>
    <p v-if="project?.job?.error_code" role="alert" class="text-status-failed">{{ videoErrorText(project.job.error_code) }} <button v-if="!active" :disabled="!canResume" @click="resume">{{ t('videoWorkspace.resume') }}</button></p>
    <div v-if="selectedTrack && !showNewProject" class="rounded-xl border border-border bg-panel p-4">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2"><strong class="text-sm">{{ t('videoExperience.currentSong') }} · {{ project?.track_title || selectedTrack.title || selectedTrack.filename }} · {{ clockText(project?.duration_sec ?? (selectedTrack.duration_ms ?? 0) / 1000) }}</strong><button v-if="selectedShot" @click="audition">{{ t('videoWorkspace.audition') }}</button></div>
      <audio ref="audio" controls preload="metadata" class="w-full" :src="selectedTrack.audio_url" :aria-label="t('videoWorkspace.sourceAudio')" @timeupdate="audioTime" @play="audioStarted" @pause="audioStopped" @ended="audioStopped" @error="audioError = t('videoWorkspace.audioFailed')"></audio>
      <p v-if="audioError" role="alert" class="text-status-failed">{{ audioError }}</p>
    </div>
    <section :id="`video-panel-${step}`" role="tabpanel" :aria-labelledby="`video-step-${step}`" tabindex="0" class="min-w-0 space-y-4">
      <div><h2 id="video-panel-heading" ref="panelHeading" tabindex="-1" class="text-xl font-semibold text-text">{{ t(step === 'song' && !showNewProject ? 'videoExperience.savedSongGoal' : `videoWorkspace.${step}Goal`) }}</h2><p v-if="step !== 'direction'" class="mt-2 text-sm text-text-dim">{{ t(`videoExperience.stageDescriptions.${step}`) }}</p></div>
      <template v-if="step === 'song'">
        <div v-if="showNewProject" data-new-video-project class="video-card space-y-4">
          <div><h3 class="font-semibold">{{ t('videoExperience.chooseSong') }}</h3><p class="mt-2 text-sm leading-relaxed text-text-dim">{{ t('videoExperience.newIntro') }}</p></div>
          <div role="group" :aria-label="t('videoExperience.newProject')" class="flex flex-wrap gap-2">
            <button type="button" :aria-pressed="startKind === 'song'" @click="startKind = 'song'">{{ t('videoExperience.startSong') }}</button>
            <button type="button" :aria-pressed="startKind === 'silent'" @click="startKind = 'silent'">{{ t('videoExperience.startSilent') }}</button>
            <button type="button" :aria-pressed="startKind === 'reel'" @click="startKind = 'reel'">{{ t('videoExperience.startReel') }}</button>
          </div>
          <template v-if="startKind === 'song'">
            <label>{{ t('videoWorkspace.searchSongs') }}<input v-model="songSearch" type="search"></label><label>{{ t('video.song') }}<select :value="trackId ?? ''" :disabled="acting" @change="chooseTrack"><option value="">{{ t('video.songPlaceholder') }}</option><option v-for="track in searchedTracks" :key="track.id" :value="track.id">{{ track.title || track.filename }} · {{ clockText((track.duration_ms ?? 0) / 1000) }}</option></select></label>
            <p class="text-sm text-text-dim">{{ t('videoExperience.sourceHint') }}</p><p v-if="!tracks.length">{{ t('video.noSongs') }}</p>
            <p v-if="!loading && !readiness?.ffmpeg_ready" role="status" class="text-sm text-status-failed">{{ readiness ? t('video.err.ffmpeg_missing') : t('videoWorkspace.readinessFailed') }}</p>
          </template>
          <template v-else-if="startKind === 'silent'">
            <p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.silentIntro') }}</p>
            <label>{{ t('videoExperience.silentLength') }}<input v-model.number="silentSeconds" type="number" min="2" max="60" step="1" :disabled="acting"></label>
          </template>
          <template v-else>
            <p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.reelIntro') }}</p>
            <label>{{ t('videoExperience.reelLength') }}<select v-model.number="reelSeconds" :disabled="acting"><option :value="8">8 s</option><option :value="10">10 s</option><option :value="12">12 s</option><option :value="14">14 s</option></select></label>
          </template>
          <div class="video-actions"><button type="button" :disabled="acting || (startKind === 'song' && (!trackId || !readiness?.ffmpeg_ready))" class="primary" @click="startKind === 'song' ? createProject() : beginPicture()">{{ acting ? t('videoExperience.newLoading') : t('videoWorkspace.newProject') }}</button><button v-if="project" type="button" :disabled="acting" @click="cancelNewProject">{{ t('videoExperience.backToProject') }}</button></div>
        </div>
        <div v-else-if="project && draft" class="video-card space-y-4">
          <div v-if="pictureProject"><p class="text-xs text-text-dim">{{ t('videoExperience.currentSong') }}</p><h3 class="mt-1 break-words font-semibold">{{ project.name }} <span class="text-sm font-normal text-text-dim">· {{ clockText(project.duration_sec) }}</span></h3><p class="mt-2 text-sm text-text-dim">{{ reelProject ? t('videoExperience.reelSaved') : t('videoExperience.silentSaved') }}</p></div>
          <div v-else><p class="text-xs text-text-dim">{{ t('videoExperience.currentSong') }}</p><h3 class="mt-1 break-words font-semibold">{{ project.track_title }} <span class="text-sm font-normal text-text-dim">· {{ clockText(project.duration_sec) }}</span></h3><p v-if="!selectedTrack" class="mt-2 text-sm text-text-dim">{{ t('videoExperience.missingSource') }}</p></div>
          <label>{{ t('videoWorkspace.projectName') }}<input v-model="draft.name" maxlength="120" :disabled="readOnly"></label>
          <div class="video-actions"><button type="button" class="primary" @click="changeStep('direction', true)">{{ t('videoWorkspace.continue') }}</button><button type="button" :disabled="acting || saving" @click="startNewProject">{{ t('videoExperience.newProject') }}</button></div>
        </div>
        <LocalEnginePanel v-if="(showNewProject && startKind !== 'song') || (!showNewProject && pictureProject)" kind="picture" />
      </template>
      <template v-else-if="draft && project && step === 'direction'">
        <VideoDirectionPanel v-model="draft" :project="project" :readiness="readiness" :read-only="readOnly" :can-analyze="canAnalyze" @analyze="analyze" @continue="changeStep('storyboard', true)" @upload="upload" @duplicate="duplicate" />
        <VideoDialoguePanel v-if="project.dialogue_cues?.length" :project="project" :read-only="readOnly" @refresh="refreshCue" />
        <VideoSoundtrackPanel v-if="pictureProject && !project.dialogue_cues?.length" :project="project" :draft="draft" :read-only="readOnly" @upload="uploadSpeech" @clear="clearSpeech" @speak="speakLine" />
        <VideoCharacterPanel v-if="pictureProject" :project="project" :read-only="readOnly" @apply="applyCharacter" />
        <VideoCharacterTrainer v-if="pictureProject" :project="project" :read-only="readOnly" @apply="applyTrainedCharacter" />
      </template>
      <template v-else-if="draft && project && step === 'storyboard'">

        <div class="video-card space-y-3">
          <h3 class="font-semibold">{{ t('videoExperience.storyboard') }}</h3>
          <div v-if="!draft.shots.length" class="video-empty"><p class="font-semibold">{{ t('videoExperience.noShots') }}</p><p class="mt-2 text-sm text-text-dim">{{ pictureProject ? t('videoExperience.noShotsPictureHint') : t('videoExperience.noShotsHint') }}</p></div>
          <div class="flex flex-wrap justify-between gap-2 text-sm"><span>{{ t('video.shotCount', { count: draft.shots.length }) }} · {{ clockText(coverageEnd) }} / {{ clockText(project.duration_sec) }}</span><span v-if="gapSeconds > 1 / 24">{{ t('videoWorkspace.gaps', { seconds: gapSeconds.toFixed(2) }) }}</span></div>
          <svg v-if="waves && !pictureProject" viewBox="0 0 1000 50" preserveAspectRatio="none" class="h-16 w-full rounded bg-panel-2" role="img" :aria-label="t('videoWorkspace.waveform')"><polyline :points="waves" fill="none" stroke="currentColor" stroke-width="1" class="text-accent1"/><polyline :points="energy" fill="none" stroke="currentColor" stroke-width="2" class="text-accent2"/></svg>
          <div class="relative h-12 rounded bg-panel-2" :aria-label="t('videoWorkspace.timeline')"><span v-for="marker in (draft.markers ?? []).filter((item) => item.kind === 'beat').slice(0, 300)" :key="marker.id" class="absolute top-0 h-2 w-px bg-text-dim" :style="{ left: `${marker.time_sec / project.duration_sec * 100}%` }" aria-hidden="true"></span><button v-for="(shot, index) in draft.shots" :key="shot.id" class="absolute top-2 min-w-1 truncate border border-border text-xs" :aria-label="t('video.shotLabel', { current: index + 1 })" :aria-pressed="selectedShotId === shot.id" :style="{ left: `${shot.start_sec / project.duration_sec * 100}%`, width: `${(shot.seconds ?? 4) / project.duration_sec * 100}%`, minHeight: '36px', padding: '4px' }" @click="selectedShotId = shot.id; seek(shot.start_sec)">{{ index + 1 }}</button></div>
          <label v-if="!pictureProject">{{ t('videoWorkspace.seekSong') }} · {{ clockText(position) }}<input v-model.number="position" type="range" min="0" :max="project.duration_sec" step="0.041666666666666664" :aria-valuetext="clockText(position)" @input="seek(position)"></label>
          <div class="flex gap-2 overflow-x-auto pb-2" :aria-label="t('videoWorkspace.shotList')"><button v-for="(shot, index) in draft.shots" :key="shot.id" :aria-pressed="selectedShotId === shot.id" :class="selectedShotId === shot.id ? 'border-accent1' : 'border-border'" class="min-w-36 max-w-48 shrink-0 rounded-lg border p-3 text-left" @click="selectedShotId = shot.id"><img v-if="shotStrip(shot.id)" :src="shotStrip(shot.id)" :alt="t('videoDialogue.filmstrip', { number: index + 1 })" loading="lazy" class="mb-2 h-12 w-full rounded object-contain bg-black" /><strong>{{ index + 1 }} · {{ clockText(shot.start_sec) }}–{{ clockText(shot.start_sec + (shot.seconds ?? 4)) }}</strong><span class="mt-1 block truncate text-xs">{{ shot.prompt }}</span><span v-if="shotProblem(draft.shots, shot.id, timelineLimit)" class="block text-xs text-status-failed">{{ t('videoWorkspace.needsFix') }}</span></button></div>
          <div class="flex flex-wrap gap-2"><button :disabled="readOnly || Boolean(project.dialogue_cues?.length) || draft.shots.length >= shotCap" @click="addShot">{{ t('video.addShot') }}</button><button :disabled="readOnly || (!undoStack.length && !project.undo_available)" @click="undo">{{ t('videoWorkspace.undo') }}</button><button :disabled="readOnly || dirty || !project.redo_available" @click="redo">{{ t('videoDialogue.redo') }}</button><label class="inline-check"><input v-model="ripple" type="checkbox">{{ t('videoWorkspace.ripple') }}</label><button v-if="!pictureProject" :disabled="!canAnalyze" @click="analyze">{{ t('videoWorkspace.reanalyze') }}</button></div>
          <p v-if="!pictureProject && readiness && !readiness.analysis_ready" class="text-sm text-status-failed">{{ t('videoWorkspace.analysisDependencyHint') }}</p>
        </div>
        <fieldset v-if="selectedShot" :disabled="readOnly" class="video-card space-y-4">
          <h3 class="font-semibold">{{ t('videoExperience.selectedShot', { number: draft.shots.findIndex(shot => shot.id === selectedShotId) + 1 }) }}</h3>
          <div class="grid gap-4 sm:grid-cols-2"><label>{{ t('video.start') }}<input v-model.number="selectedShot.start_sec" type="number" min="0" :max="project.duration_sec" :disabled="Boolean(project.dialogue_cues?.length)" step="0.041666666666666664" @change="selectedShot.start_sec = frameTime(selectedShot.start_sec)"></label><div><span class="mb-1 block text-sm">{{ t('video.length') }}</span><div role="group" :aria-label="t('video.length')" class="flex flex-wrap gap-2"><button v-for="seconds in clipLengths" :key="seconds" type="button" :disabled="Boolean(project.dialogue_cues?.length)" :aria-pressed="selectedShot.seconds === seconds" @click="length(seconds)">{{ seconds }} s</button></div></div></div>
          <label v-if="generatedMode">{{ t('video.prompt') }}<textarea v-model="selectedShot.prompt" data-testid="video-shot-prompt" rows="4" maxlength="2000"></textarea><span class="text-xs text-text-dim">{{ selectedShot.prompt.length }} / 2000 · {{ t('videoWorkspace.promptHint') }}</span></label>
          <p v-else class="text-sm text-text-dim">{{ t('videoExperience.imageShotHint') }}</p>
          <label v-if="!generatedMode && repairDescriptionFor === selectedShot.id">{{ t('videoExperience.repairDescription') }}<textarea v-model="selectedShot.prompt" data-video-shot-description-repair rows="2" maxlength="2000"></textarea><span class="text-xs text-text-dim">{{ t('videoExperience.repairDescriptionHint') }}</span></label>
          <label>{{ t('videoWorkspace.reference') }}<select v-model="selectedShot.reference_id"><option :value="null">{{ pictureProject && (project.references?.length ?? 0) === 1 ? t('videoWorkspace.firstImage') : generatedMode ? t('videoWorkspace.none') : t('videoWorkspace.firstImage') }}</option><option v-for="image in project.references" :key="image.id" :value="image.id">{{ image.name }}</option></select></label>
          <p v-if="pictureProject && draft.character_lock" class="text-sm text-text-dim">{{ t('videoDirection.characterLockHint') }}</p>
          <p v-if="characterIssue === 'missing'" role="alert" class="text-sm text-status-failed">{{ t('videoDirection.characterMissing') }}</p>
          <p v-if="characterIssue === 'mismatch'" role="alert" class="text-sm text-status-failed">{{ t('videoDirection.characterMismatch') }}</p>
          <details class="video-secondary"><summary>{{ t('videoExperience.shotSettings') }}</summary><div class="mt-4 space-y-4">
            <div class="grid gap-4 sm:grid-cols-2"><label>{{ t('videoWorkspace.seed') }}<input v-model.number="selectedShot.seed" type="number" min="0" max="2147483647"></label><label v-if="generatedMode && !draft.character_lock">{{ t('videoWorkspace.strength') }}<input v-model.number="selectedShot.reference_strength" type="range" min="0" max="1" step="0.05"><span>{{ selectedShot.reference_strength ?? 0.7 }}</span></label><p v-else-if="generatedMode && draft.character_lock" class="text-sm text-text-dim">{{ t('videoWorkspace.fixedStrength') }}</p></div>
            <label class="inline-check"><input v-model="selectedShot.locked" type="checkbox">{{ t('videoWorkspace.lock') }}</label>
            <div class="video-actions" v-if="!project.dialogue_cues?.length"><button type="button" @click="editShots(moveShot(draft.shots, selectedShotId, -1))">{{ t('videoWorkspace.moveEarlier') }}</button><button type="button" @click="editShots(moveShot(draft.shots, selectedShotId, 1))">{{ t('videoWorkspace.moveLater') }}</button><button type="button" :disabled="selectedShot.seconds === 2 || draft.shots.length >= shotCap" @click="editShots(splitShot(draft.shots, selectedShotId, newVideoId()))">{{ t('videoWorkspace.split') }}</button><button type="button" :disabled="draft.shots.length >= shotCap" @click="editShots(duplicateShot(draft.shots, selectedShotId, newVideoId()))">{{ t('videoWorkspace.duplicate') }}</button><button type="button" class="text-status-failed" @click="removeShot">{{ t('video.removeShot') }}</button></div>
          </div></details>
          <p v-if="shotProblem(draft.shots, selectedShot.id, timelineLimit)" role="alert" class="text-status-failed">{{ videoErrorText(shotProblem(draft.shots, selectedShot.id, timelineLimit)) }}</p>
        </fieldset>
        <details v-if="!pictureProject" class="video-card video-secondary"><summary>{{ t('videoExperience.markers') }}</summary><div class="mt-4 space-y-2"><div class="flex flex-wrap items-center justify-between gap-2"><h3>{{ t('videoWorkspace.markers') }} <span v-if="project.analysis?.tempo_bpm" class="text-text-dim">~{{ Math.round(project.analysis.tempo_bpm) }} BPM</span></h3><button :disabled="readOnly" @click="addMarker">{{ t('videoWorkspace.addMarker') }}</button></div><p class="text-xs text-text-dim">{{ t('videoWorkspace.markerHint') }}</p><div class="max-h-48 overflow-y-auto space-y-1"><div v-for="marker in importantMarkers" :key="marker.id" class="flex flex-wrap items-center gap-2 text-sm"><button @click="seek(marker.time_sec)">{{ clockText(marker.time_sec) }} · {{ marker.label || marker.kind }}</button><span>{{ Math.round((marker.confidence ?? 0) * 100) }}%</span><button :disabled="readOnly || !selectedShot" @click="snapShot(marker)">{{ t('videoWorkspace.snap') }}</button><button v-if="marker.kind === 'manual'" :disabled="readOnly" @click="draft.markers = draft.markers.filter((item) => item.id !== marker.id)">{{ t('video.removeShot') }}</button></div></div></div></details>
        <div class="flex flex-wrap gap-2"><button :disabled="!canCompute || !selectedShot" class="primary" @click="preview()">{{ t('videoWorkspace.previewShot') }}</button><button type="button" @click="changeStep('preview', true)">{{ t('videoWorkspace.reviewPreviews') }}</button></div>
        <p v-if="computeBlocker" role="status" class="text-sm text-status-failed">{{ computeBlocker }}</p><p class="text-sm text-text-dim">{{ t('videoWorkspace.previewHint') }}</p>
      </template>
      <template v-else-if="draft && project && step === 'preview'">

        <fieldset :disabled="readOnly" class="video-card space-y-3"><h3 class="font-semibold">{{ t('videoExperience.previewSettings') }}</h3><label>{{ t('videoWorkspace.variantCount') }}<select v-model.number="variantsPerShot"><option :value="1">1</option><option :value="2">2</option><option :value="3" :disabled="!generatedMode">3</option></select></label><p v-if="!generatedMode" class="text-sm text-text-dim">{{ t('videoWorkspace.imageMotionHint') }}</p><div class="flex flex-wrap gap-3"><label v-for="(shot, index) in draft.shots" :key="shot.id" class="inline-check"><input v-model="selectedPreviewIds" type="checkbox" :value="shot.id">{{ index + 1 }} · {{ clockText(shot.start_sec) }}</label></div><button :disabled="!canCompute || !selectedPreviewIds.length" class="primary" @click="preview(selectedPreviewIds)">{{ t('videoWorkspace.previewSelected') }}</button></fieldset>
        <div class="flex gap-2 overflow-x-auto"><button v-for="(shot, index) in draft.shots" :key="shot.id" :aria-pressed="selectedShotId === shot.id" @click="selectedShotId = shot.id">{{ t('video.shotLabel', { current: index + 1 }) }} <span v-if="project.shots?.find((item) => item.id === shot.id)?.approved_variant_id">✓</span></button></div>
        <div v-if="!shotVariants.length" class="video-empty"><p class="font-semibold">{{ t('videoExperience.previewEmpty') }}</p><p class="mt-2 text-sm text-text-dim">{{ t('videoExperience.previewEmptyHint') }}</p></div>
        <div class="grid gap-4 md:grid-cols-2"><article v-for="variant in shotVariants" :key="variant.id" class="rounded-xl border bg-panel p-4 space-y-3" :class="savedShot?.approved_variant_id === variant.id ? 'border-accent1' : 'border-border'">
          <div class="flex items-center justify-between gap-2"><strong>{{ t('videoWorkspace.seed') }} {{ variant.seed }}</strong><span>{{ t(`videoWorkspace.status.${variant.status ?? 'queued'}`) }}</span></div>
          <VideoPreviewPlayer v-if="variant.status === 'ready' && variant.file_url" :src="variant.file_url" :poster="variant.poster_url" :label="t('videoWorkspace.variantVideo', { seed: variant.seed })" />
          <img v-if="variant.filmstrip_url" :src="variant.filmstrip_url" :alt="t('videoDialogue.filmstrip', { number: draft.shots.findIndex(shot => shot.id === selectedShotId) + 1 })" loading="lazy" class="w-full rounded bg-black" />
          <p v-if="variant.error_code" class="text-status-failed">{{ videoErrorText(variant.error_code) }}</p>
          <details><summary>{{ t('videoWorkspace.details') }}</summary><p class="mt-2 whitespace-pre-wrap text-sm">{{ variant.prompt || selectedShot?.prompt }}</p><p class="text-xs text-text-dim">{{ variant.settings?.engine_pack }} · {{ variant.settings?.width }}×{{ variant.settings?.height }} · {{ variant.settings?.stage1_steps }} + {{ variant.settings?.stage2_steps }} · CFG {{ variant.settings?.cfg_scale }}</p><p v-for="timing in variant.timings" :key="timing.started_at" class="text-xs">{{ timing.phase }} · {{ clockText(timing.duration_sec ?? 0) }}</p></details>
          <button v-if="variant.status === 'ready'" :disabled="readOnly || savedShot?.approved_variant_id === variant.id" @click="approve(selectedShotId, variant.id)">{{ savedShot?.approved_variant_id === variant.id ? t('videoWorkspace.approved') : t('videoWorkspace.approve') }}</button>
        </article></div>
        <p v-if="computeBlocker" role="status" class="text-sm text-status-failed">{{ computeBlocker }}</p><div class="video-actions"><button :disabled="!canCompute || !selectedShot" @click="preview()">{{ t('videoWorkspace.retryShot') }}</button><button type="button" @click="changeStep('storyboard', true)">{{ t('videoWorkspace.editStoryboard') }}</button><button type="button" class="primary" @click="changeStep('export', true)">{{ t('videoWorkspace.continueExport') }}</button></div>
      </template>
      <template v-else-if="draft && project && step === 'export'">
<p class="text-sm font-medium text-text">{{ t('videoExperience.approvalProgress', { approved: approvalCount, total: draft.shots.length }) }}</p>
        <fieldset :disabled="readOnly" class="rounded-xl bg-panel p-4 space-y-4"><div class="grid gap-4 sm:grid-cols-2"><label>{{ t('videoWorkspace.aspect') }}<select v-model="draft.export_settings.aspect"><option value="landscape">16:9</option><option value="portrait">9:16</option><option value="square">1:1</option></select></label><label>{{ t('videoWorkspace.encodeQuality') }}<select v-model="draft.export_settings.quality"><option value="fast">{{ t('video.qualityFaster') }}</option><option value="standard">{{ t('video.qualityStandard') }}</option><option value="high">{{ t('videoWorkspace.high') }}</option></select></label></div><p class="text-xs text-text-dim">{{ reelProject && draft.export_settings.aspect === 'portrait' ? t('videoDirection.reelSizeHint') : pictureProject ? t('videoExperience.silentExport') : t('videoWorkspace.exportHint') }}</p>
          <div v-if="pictureProject" data-talking-voice class="space-y-3 border-t border-border pt-4"><h3 class="font-semibold">{{ t('videoExperience.speechTitle') }}</h3><p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.speechHint') }}</p><p v-if="project.speech_clip">{{ project.speech_clip.name }} · {{ clockText(project.speech_clip.duration_sec) }}</p><SpeechAudioPreview v-if="project.speech_clip" :key="project.speech_clip.id" :src="`/api/videos/projects/${project.id}/speech?clip=${project.speech_clip.id}`" :label="t('videoExperience.speechFile')" /><label class="inline-check"><input v-model="draft.export_settings.attach_speech" type="checkbox" :disabled="!project.speech_clip">{{ t('videoExperience.speechAttach') }}</label><p class="text-sm text-text-dim">{{ draft.export_settings.attach_speech && project.speech_clip ? t('videoExperience.soundtrackOn') : t('videoExperience.soundtrackOff') }}</p></div>
          <details class="video-secondary"><summary>{{ t('videoExperience.textOptions') }}</summary><div class="mt-4 space-y-4"><label class="inline-check"><input v-model="draft.export_settings.include_overlays" type="checkbox">{{ t('videoWorkspace.includeText') }}</label><div class="flex justify-between gap-2"><h3>{{ t('videoWorkspace.overlays') }}</h3><button :disabled="draft.overlays.length >= 100" @click="addOverlay">{{ t('videoWorkspace.addText') }}</button></div>
          <div v-for="overlay in draft.overlays" :key="overlay.id" class="space-y-2 rounded-lg bg-panel-2 p-3"><label>{{ t('videoWorkspace.text') }}<textarea v-model="overlay.text" rows="2" maxlength="500"></textarea></label><div class="grid gap-2 sm:grid-cols-4"><label>{{ t('video.start') }}<input v-model.number="overlay.start_sec" type="number" min="0" :max="project.duration_sec" step="0.1"></label><label>{{ t('videoWorkspace.end') }}<input v-model.number="overlay.end_sec" type="number" min="0" :max="project.duration_sec" step="0.1"></label><label>{{ t('videoWorkspace.position') }}<select v-model="overlay.position"><option value="top">{{ t('videoWorkspace.top') }}</option><option value="center">{{ t('videoWorkspace.center') }}</option><option value="bottom">{{ t('videoWorkspace.bottom') }}</option></select></label><label>{{ t('videoWorkspace.textSize') }}<input v-model.number="overlay.font_size" type="number" min="14" max="96"></label></div><label>{{ t('videoWorkspace.color') }}<input v-model="overlay.color" type="color"></label><button @click="draft.overlays = draft.overlays.filter((item) => item.id !== overlay.id)">{{ t('video.removeShot') }}</button></div>
          </div></details>
        </fieldset>
        <div class="grid gap-4 md:grid-cols-2">
          <article data-video-render-action class="video-card flex flex-col gap-3"><h3 class="font-semibold">{{ t('videoExperience.renderTitle') }}</h3><p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.renderDescription') }}</p><p v-if="assemblyBlocker" class="text-sm text-status-failed">{{ assemblyBlocker }}</p><button type="button" class="primary mt-auto" :disabled="!canAssemble" @click="render">{{ t('videoWorkspace.render') }}</button></article>
          <article data-video-export-action class="video-card flex flex-col gap-3"><h3 class="font-semibold">{{ t('videoExperience.exportTitle') }}</h3><p class="text-sm leading-relaxed text-text-dim">{{ t('videoExperience.exportDescription') }}</p><p v-if="exportBlocker" class="text-sm text-status-failed">{{ exportBlocker }}</p><button type="button" class="mt-auto" :disabled="!canExport || approvalCount !== draft.shots.length" @click="exportVideo">{{ t('videoWorkspace.exportApproved') }}</button></article>
        </div>
        <button v-if="project.job?.status === 'failed' || project.job?.status === 'cancelled'" :disabled="!canResume" @click="resume">{{ t('videoWorkspace.resume') }}</button>
        <div class="video-card space-y-4"><h3 class="font-semibold">{{ t('videoExperience.finished') }}</h3>
        <VideoPreviewPlayer v-if="project.file_url" :key="exportSource" :src="exportSource" :poster="exportPoster" :label="t('videoWorkspace.finishedVideo')" /><a v-if="project.file_url" :href="exportSource" download class="inline-block min-h-11 rounded-lg bg-accent1 px-4 py-3 text-white">{{ t('common.download') }}</a>
        <div v-if="!project.file_url" class="video-empty"><p class="font-medium">{{ t('videoExperience.noOutput') }}</p><p class="mt-2 text-sm text-text-dim">{{ t('videoExperience.noOutputHint') }}</p></div></div>
      </template>
      <p v-else class="text-text-dim">{{ t('videoWorkspace.chooseSong') }} <button @click="step = 'song'">{{ t('videoWorkspace.song') }}</button></p>
    </section>
    <template v-for="item in videoWorkspaceSteps" :key="item"><div v-if="item !== step" :id="`video-panel-${item}`" role="tabpanel" :aria-labelledby="`video-step-${item}`" hidden></div></template>
      </div>
    </div>
    <details v-if="legacyVideos.length" class="rounded-xl bg-panel p-4"><summary>{{ t('videoWorkspace.previousVideos') }} ({{ legacyVideos.length }})</summary><div class="mt-4 grid gap-3 sm:grid-cols-2"><label>{{ t('videoWorkspace.search') }}<input v-model="search" type="search"></label><label>{{ t('videoWorkspace.filter') }}<select v-model="statusFilter"><option value="all">{{ t('videoWorkspace.all') }}</option><option v-for="status in ['ready', 'failed', 'cancelled', 'running', 'queued']" :key="status" :value="status">{{ t(`videoWorkspace.status.${status}`) }}</option></select></label></div><ul class="mt-4 grid gap-4 md:grid-cols-2"><li v-for="row in visibleVideos" :key="row.id" class="rounded-lg bg-panel-2 p-3 space-y-2"><strong>{{ row.title }}</strong> · {{ t(`videoWorkspace.status.${row.status}`) }}<VideoPreviewPlayer v-if="row.status === 'ready' && row.file_url" :src="row.file_url" :label="row.title" /><p v-if="row.error_code" class="text-status-failed">{{ videoErrorText(row.error_code) }}</p><div class="flex gap-2"><a v-if="row.file_url" :href="row.file_url" download>{{ t('common.download') }}</a><button :disabled="isVideoActive(row.status)" @click="removeLegacy(row.id)">{{ t('video.delete') }}</button></div></li></ul><div class="mt-3 flex justify-between"><button :disabled="page <= 1" @click="page--">{{ t('videoWorkspace.previous') }}</button><span>{{ page }} / {{ Math.max(1, Math.ceil(filteredVideos.length / 10)) }}</span><button :disabled="page * 10 >= filteredVideos.length" @click="page++">{{ t('videoWorkspace.next') }}</button></div></details>
  </div>
</template>

<style scoped>
.video-project-layout { display: grid; gap: 24px; min-width: 0; }
.video-library-summary { display: flex; align-items: center; justify-content: space-between; min-height: 44px; cursor: pointer; border: 1px solid var(--color-border); border-radius: 10px; padding: 10px 14px; background: var(--color-panel); font-size: 14px; font-weight: 600; }
.video-stage-nav { display: flex; gap: 6px; overflow-x: auto; padding: 2px 2px 6px; }
.video-workspace button.video-stage { display: flex; flex: 1 0 126px; align-items: center; gap: 8px; min-height: 64px; border: 1px solid transparent; background: var(--color-panel-2); padding: 10px; }
.video-workspace button.video-stage[aria-selected=true] { border-color: var(--color-accent2); background: color-mix(in srgb, var(--color-accent1) 16%, var(--color-panel)); color: var(--color-text); }
.video-stage-number { display: flex; height: 24px; width: 24px; flex-shrink: 0; align-items: center; justify-content: center; border-radius: 50%; background: var(--color-panel); font-size: 12px; }
.video-card { min-width: 0; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-panel); padding: 20px; }
.video-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.video-empty { border: 1px dashed var(--color-border); border-radius: 10px; padding: 28px 20px; text-align: center; }
.video-secondary summary { min-height: 44px; padding-block: 10px; cursor: pointer; font-size: 14px; font-weight: 500; }
.video-notice { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; border: 1px solid color-mix(in srgb, var(--color-status-failed) 40%, transparent); border-radius: 10px; padding: 14px; color: var(--color-status-failed); font-size: 14px; }
.video-notice p { flex: 1 1 240px; }
.video-workspace h2:focus-visible, .video-workspace summary:focus-visible { outline: 2px solid var(--color-accent2); outline-offset: 2px; }
.video-workspace label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
.video-step-status { top: calc(var(--app-header-height, 44px) + .5rem); }
.video-workspace input:not([type=checkbox]):not([type=range]):not([type=color]), .video-workspace select, .video-workspace textarea { width: 100%; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
.video-workspace button { min-height: 44px; padding: .5rem .75rem; border-radius: .5rem; background-color: var(--color-panel-2); }
.video-workspace button.primary, .video-workspace button[aria-selected=true], .video-workspace button[aria-pressed=true] { background: var(--color-accent1); color: white; }
.video-workspace button:disabled { opacity: .45; cursor: not-allowed; }
.video-workspace button:focus-visible, .video-workspace input:focus-visible, .video-workspace select:focus-visible, .video-workspace textarea:focus-visible { outline: 2px solid var(--color-accent1); outline-offset: 2px; }
.video-workspace label.inline-check { flex-direction: row; align-items: center; min-height: 44px; }
.video-workspace input[type=checkbox] { width: 18px; height: 18px; }
@media (min-width: 1280px) { .video-project-layout { grid-template-columns: 244px minmax(0, 1fr); align-items: start; } .video-library-summary { display: none; } }
@media (max-width: 540px) { .video-card { padding: 16px; } .video-step-status { position: static; } }
</style>
