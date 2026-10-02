<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useVideoWorkspace } from './useVideoWorkspace'
import VideoPreviewPlayer from './VideoPreviewPlayer.vue'
import VideoProjectLibrary from './VideoProjectLibrary.vue'
import { videoWorkspaceSteps, videoClipLengths, changeShotLength, splitShot, duplicateShot, moveShot, newVideoId, frameTime, shotProblem, type VideoWorkspaceStep, type VideoClipLength } from './videoWorkspace'
import { videoErrorText, videoRequestError, deleteVideo, isVideoActive } from '../../api/videos'
import type { VideoProjectJob, VideoMarker } from '../../api/contracts'
import { formatClock } from '../../composables/voicePace'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import { notePhasePace, phaseRemaining, type VideoPhasePace } from './videoJobTiming'

const { t } = useI18n()
const { tracks, projects, legacyVideos, project, draft, step, selectedShotId, selectedPreviewIds, variantsPerShot, trackId,
  selectedTrack, selectedShot, savedShot, loading, acting, saving, dirty, error, saveError, serverBusy, readiness, now, undoStack, active, readOnly, problem, coverageEnd, approvalCount,
  save, selectProject, removeProject, reloadProject, createProject, preview, render, analyze, approve, resume, cancel, duplicate, exportVideo, upload, editShots, undo, addShot } = useVideoWorkspace()
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
const projectManagerOpen = ref(false)
const searchedTracks = computed(() => tracks.value.filter((track) => `${track.title} ${track.filename} ${track.short_id ?? ''}`.toLowerCase().includes(songSearch.value.toLowerCase())))
const engineOption = computed(() => readiness.value?.options.find((option) => option.id === (draft.value?.settings.engine_pack ?? 'ltx23')))
const generatedMode = computed(() => draft.value?.mode === 'generated')
const modeReady = computed(() => Boolean(readiness.value?.ffmpeg_ready) && (!generatedMode.value || Boolean(engineOption.value?.available)))
const canAnalyze = computed(() => !readOnly.value && !serverBusy.value && Boolean(readiness.value?.analysis_ready && readiness.value.ffmpeg_ready) && !project.value?.source_changed)
const gib = (bytes: number) => (bytes / 1024 ** 3).toFixed(1)
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
const shotVariants = computed(() => savedShot.value?.variants ?? [])
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
function chooseProject(event: Event) { if (event.target instanceof HTMLSelectElement) void selectProject(event.target.value) }
function chooseTrack(event: Event) { if (event.target instanceof HTMLSelectElement) trackId.value = Number(event.target.value) || null }
function changeStep(value: VideoWorkspaceStep) { step.value = value }
function stepKey(event: KeyboardEvent, index: number) {
  const target = event.key === 'Home' ? 0 : event.key === 'End' ? 4 : event.key === 'ArrowRight' ? (index + 1) % 5 : event.key === 'ArrowLeft' ? (index + 4) % 5 : null
  if (target === null) return
  event.preventDefault()
  const next = videoWorkspaceSteps[target]
  if (next) { step.value = next; document.getElementById(`video-step-${next}`)?.focus() }
}
function stepDone(value: VideoWorkspaceStep) {
  if (!project.value || !draft.value) return false
  return value === 'song' ? Boolean(project.value) : value === 'direction' ? Boolean(draft.value?.direction || draft.value?.mode !== 'generated')
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
function setSize(event: Event) {
  if (!draft.value || !(event.target instanceof HTMLSelectElement)) return
  if (event.target.value === '1280') { draft.value.settings.width = 1280; draft.value.settings.height = 704 }
  else if (event.target.value === '768') { draft.value.settings.width = 768; draft.value.settings.height = 512 }
  else { draft.value.settings.width = 704; draft.value.settings.height = 448 }
}
function filesChanged(event: Event) {
  if (event.target instanceof HTMLInputElement && event.target.files?.[0]) { void upload(event.target.files[0]); event.target.value = '' }
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
  if (draft.value && project.value) draft.value.overlays.push({ id: newVideoId(), kind: 'title', text: project.value.track_title,
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
watch(() => selectedTrack.value?.audio_url, () => { stopSource(); position.value = 0; auditionEnd.value = null; audioError.value = '' })
onBeforeUnmount(stopSource)
</script>

<template>
  <div class="video-workspace mx-auto max-w-6xl space-y-5 p-4 sm:p-6">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div><h1 class="text-2xl font-semibold">{{ t('videoWorkspace.title') }}</h1><p class="mt-1 text-sm text-text-dim">{{ t('videoWorkspace.intro') }}</p></div>
      <div class="flex flex-wrap items-end gap-2">
        <label v-if="projects.length" class="text-sm">{{ t('videoWorkspace.project') }}
          <select :value="project?.id ?? ''" :disabled="acting || saving" @change="chooseProject"><option value="" disabled>{{ t('videoWorkspace.noProject') }}</option><option v-for="row in projects" :key="row.id" :value="row.id">{{ row.name }} · {{ row.track_title }}</option></select>
        </label>
        <button type="button" :aria-expanded="projectManagerOpen" aria-controls="video-project-library" :disabled="acting || saving" @click="projectManagerOpen = !projectManagerOpen">{{ t('videoWorkspace.manageProjects') }}</button>
      </div>
    </header>
    <VideoProjectLibrary v-if="projectManagerOpen" :projects="projects" :selected-id="project?.id" :busy="acting || saving" @open="selectProject" @delete="removeProject" />
    <div class="video-step-status sticky z-10 space-y-2 rounded-xl bg-panel p-3 shadow-sm">
      <nav role="tablist" :aria-label="t('videoWorkspace.steps')" class="grid grid-cols-5 gap-1">
        <button v-for="(item, index) in videoWorkspaceSteps" :id="`video-step-${item}`" :key="item" role="tab" :aria-label="`${index + 1} ${t(`videoWorkspace.${item}`)}`" :aria-controls="`video-panel-${item}`" :aria-selected="step === item" :tabindex="step === item ? 0 : -1"
          :class="step === item ? 'bg-accent1 text-white' : 'text-text-dim'" class="min-h-11 rounded-lg px-1 py-2 text-sm" @click="changeStep(item)" @keydown="stepKey($event, index)">{{ index + 1 }} {{ t(`videoWorkspace.${item}`) }}<span v-if="stepDone(item)" aria-hidden="true"> ✓</span></button>
      </nav>
      <div data-testid="video-global-status" class="flex flex-wrap items-center justify-between gap-2 text-sm">
        <div><strong>{{ project?.name || t('videoWorkspace.noProject') }}</strong> · <span role="status">{{ t(`videoWorkspace.status.${statusText}`) }}<span v-if="project?.job"> · {{ jobDetail(project.job) }}</span></span>
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
    <p v-if="project?.source_changed" role="alert" class="text-status-failed">{{ t('videoWorkspace.sourceChanged') }}</p>
    <p v-if="project?.job?.error_code" role="alert" class="text-status-failed">{{ videoErrorText(project.job.error_code) }} <button v-if="!active" :disabled="!canResume" @click="resume">{{ t('videoWorkspace.resume') }}</button></p>
    <div v-if="selectedTrack" class="rounded-xl bg-panel p-4">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2"><strong class="text-sm">{{ selectedTrack.title || selectedTrack.filename }} · {{ clockText(project?.duration_sec ?? (selectedTrack.duration_ms ?? 0) / 1000) }}</strong><button v-if="selectedShot" @click="audition">{{ t('videoWorkspace.audition') }}</button></div>
      <audio ref="audio" controls preload="metadata" class="w-full" :src="selectedTrack.audio_url" :aria-label="t('videoWorkspace.sourceAudio')" @timeupdate="audioTime" @play="audioStarted" @pause="audioStopped" @ended="audioStopped" @error="audioError = t('videoWorkspace.audioFailed')"></audio>
      <p v-if="audioError" role="alert" class="text-status-failed">{{ audioError }}</p>
    </div>
    <section :id="`video-panel-${step}`" role="tabpanel" :aria-labelledby="`video-step-${step}`" tabindex="0" class="space-y-4">
      <template v-if="step === 'song'">
        <h2 class="text-lg font-semibold">{{ t('videoWorkspace.songGoal') }}</h2>
        <div class="rounded-xl bg-panel p-4 space-y-3"><label>{{ t('videoWorkspace.searchSongs') }}<input v-model="songSearch" type="search"></label><label>{{ t('video.song') }}<select :value="trackId ?? ''" :disabled="acting" @change="chooseTrack"><option value="">{{ t('video.songPlaceholder') }}</option><option v-for="track in searchedTracks" :key="track.id" :value="track.id">{{ track.title || track.filename }} · {{ clockText((track.duration_ms ?? 0) / 1000) }}</option></select></label>
          <p v-if="!tracks.length">{{ t('video.noSongs') }}</p>
          <p v-if="!loading && !readiness?.ffmpeg_ready" role="status" class="text-sm text-text-dim">{{ readiness ? t('video.err.ffmpeg_missing') : t('videoWorkspace.readinessFailed') }}</p>
          <button :disabled="!trackId || acting || !readiness?.ffmpeg_ready" class="primary" @click="createProject">{{ t('videoWorkspace.newProject') }}</button>
          <template v-if="draft"><label>{{ t('videoWorkspace.projectName') }}<input v-model="draft.name" maxlength="120" :disabled="readOnly"></label><button @click="step = 'direction'">{{ t('videoWorkspace.continue') }}</button></template>
        </div>
      </template>
      <template v-else-if="draft && project && step === 'direction'">
        <h2 class="text-lg font-semibold">{{ t('videoWorkspace.directionGoal') }}</h2>
        <fieldset :disabled="readOnly" class="grid gap-4 rounded-xl bg-panel p-4 md:grid-cols-2">
          <label>{{ t('videoWorkspace.mode') }}<select v-model="draft.mode"><option value="generated">{{ t('videoWorkspace.generated') }}</option><option value="cover">{{ t('videoWorkspace.cover') }}</option><option value="visualizer">{{ t('videoWorkspace.visualizer') }}</option></select></label>
          <label>{{ t('videoWorkspace.newShotSeed') }}<input v-model.number="draft.seed" type="number" min="0" max="2147483647"><span class="text-xs text-text-dim">{{ t('videoWorkspace.seedHint') }}</span></label>
          <label class="md:col-span-2">{{ t('videoWorkspace.directionPrompt') }}<textarea v-model="draft.direction" :disabled="!generatedMode" rows="3" maxlength="2000" :placeholder="t('video.promptPlaceholder')"></textarea><span class="text-xs text-text-dim">{{ t('videoWorkspace.directionHint') }}</span></label>
          <label>{{ t('video.size') }}<select :value="draft.settings.width" @change="setSize"><option value="704">704×448</option><option value="768">768×512</option><option value="1280">1280×704</option></select><span class="text-xs text-text-dim">{{ t('videoWorkspace.sizeHint') }}</span></label>
          <label>{{ t('videoWorkspace.engine') }}<select v-model="draft.settings.engine_pack" :disabled="!generatedMode"><option value="ltx23">LTX-2.3</option><option value="ltx25" :disabled="!readiness?.options.find((item) => item.id === 'ltx25')?.available">LTX-2.5 · {{ t('videoWorkspace.experimental') }}</option></select><span class="text-xs text-text-dim">{{ t('videoWorkspace.engineHint') }}</span></label>
          <label>{{ t('video.denoiseSteps') }}<input v-model.number="draft.settings.stage1_steps" :disabled="!generatedMode" type="number" min="10" max="50"></label>
          <label>{{ t('video.refineSteps') }}<input v-model.number="draft.settings.stage2_steps" :disabled="!generatedMode" type="number" min="1" max="3"><span class="text-xs text-text-dim">{{ t('videoWorkspace.refineHint') }}</span></label>
          <label>{{ t('video.guidance') }}<input v-model.number="draft.settings.cfg_scale" :disabled="!generatedMode" type="number" min="1" max="8" step="0.1"></label>
          <label>{{ t('videoWorkspace.negative') }}<input v-model="draft.settings.negative_prompt" :disabled="!generatedMode" maxlength="400"></label>
        </fieldset>
        <div class="rounded-xl bg-panel p-4 space-y-2 text-sm"><h3>{{ t('videoWorkspace.readiness') }}</h3><template v-if="readiness"><p>{{ t('videoWorkspace.tools') }}: {{ readiness.ffmpeg_ready ? '✓ FFmpeg' : '✗ FFmpeg' }} · {{ readiness.overlay_ready ? '✓ ' + t('videoWorkspace.text') : '✗ ' + t('videoWorkspace.text') }}</p><template v-if="draft.mode === 'generated' && engineOption"><p>{{ engineOption.name }} · {{ engineOption.available ? t('videoWorkspace.installed') : videoErrorText(engineOption.reason) }}</p><p>{{ t('videoWorkspace.disk', { total: gib(engineOption.total_bytes), missing: gib(engineOption.uncached_bytes), free: gib(engineOption.free_bytes) }) }}</p><p v-for="warning in engineOption.warnings" :key="warning">{{ videoErrorText(warning) }}</p></template><p v-for="warning in readiness.warnings" :key="warning">{{ videoErrorText(warning) }}</p></template><p v-else>{{ t('videoWorkspace.readinessFailed') }}</p><p v-if="draft.mode === 'generated' && !engineOption?.available">{{ t('videoWorkspace.setupHint') }}</p><p v-if="draft.mode !== 'generated'">{{ t('videoWorkspace.cpuMode') }}</p></div>
        <div class="rounded-xl bg-panel p-4 space-y-3"><h3>{{ t('videoWorkspace.references') }}</h3><p class="text-sm text-text-dim">{{ t('videoWorkspace.referenceHint') }}</p><label>{{ t('videoWorkspace.uploadImage') }}<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="readOnly || !readiness?.ffmpeg_ready || (project.references?.length ?? 0) >= 6" @change="filesChanged"></label>
          <div class="flex flex-wrap gap-3"><figure v-for="image in project.references" :key="image.id" class="w-32"><img :src="image.url" :alt="image.name" class="h-24 w-32 rounded object-cover"><figcaption class="truncate text-xs">{{ image.name }} · {{ image.width }}×{{ image.height }}</figcaption></figure></div>
          <p v-if="draft.mode !== 'generated' && !project.references?.length" class="text-status-failed">{{ t('videoWorkspace.imageRequired') }}</p>
        </div>
        <p v-if="!generatedMode" class="text-sm text-text-dim">{{ t('videoWorkspace.imageMotionHint') }}</p>
        <p v-if="readiness && !readiness.analysis_ready" role="status" class="text-sm text-status-failed">{{ t('videoWorkspace.analysisDependencyHint') }}</p>
        <div class="flex flex-wrap gap-2"><button :disabled="readOnly" @click="duplicate">{{ t('videoWorkspace.duplicateProject') }}</button><button :disabled="!canAnalyze" class="primary" @click="analyze">{{ t('video.analyze') }}</button><button @click="step = 'storyboard'">{{ t('videoWorkspace.editStoryboard') }}</button></div>
        <p class="text-sm text-text-dim">{{ t('videoWorkspace.analysisHint') }}</p>
      </template>
      <template v-else-if="draft && project && step === 'storyboard'">
        <h2 class="text-lg font-semibold">{{ t('videoWorkspace.storyboardGoal') }}</h2>
        <div class="rounded-xl bg-panel p-4 space-y-3">
          <div class="flex flex-wrap justify-between gap-2 text-sm"><span>{{ t('video.shotCount', { count: draft.shots.length }) }} · {{ clockText(coverageEnd) }} / {{ clockText(project.duration_sec) }}</span><span v-if="gapSeconds > 1 / 24">{{ t('videoWorkspace.gaps', { seconds: gapSeconds.toFixed(2) }) }}</span></div>
          <svg v-if="waves" viewBox="0 0 1000 50" preserveAspectRatio="none" class="h-16 w-full rounded bg-panel-2" role="img" :aria-label="t('videoWorkspace.waveform')"><polyline :points="waves" fill="none" stroke="currentColor" stroke-width="1" class="text-accent1"/><polyline :points="energy" fill="none" stroke="currentColor" stroke-width="2" class="text-accent2"/></svg>
          <div class="relative h-12 rounded bg-panel-2" :aria-label="t('videoWorkspace.timeline')"><span v-for="marker in (draft.markers ?? []).filter((item) => item.kind === 'beat').slice(0, 300)" :key="marker.id" class="absolute top-0 h-2 w-px bg-text-dim" :style="{ left: `${marker.time_sec / project.duration_sec * 100}%` }" aria-hidden="true"></span><button v-for="(shot, index) in draft.shots" :key="shot.id" class="absolute top-2 min-w-1 truncate border border-border text-xs" :aria-label="t('video.shotLabel', { current: index + 1 })" :aria-pressed="selectedShotId === shot.id" :style="{ left: `${shot.start_sec / project.duration_sec * 100}%`, width: `${(shot.seconds ?? 4) / project.duration_sec * 100}%`, minHeight: '36px', padding: '4px' }" @click="selectedShotId = shot.id; seek(shot.start_sec)">{{ index + 1 }}</button></div>
          <label>{{ t('videoWorkspace.seekSong') }} · {{ clockText(position) }}<input v-model.number="position" type="range" min="0" :max="project.duration_sec" step="0.041666666666666664" :aria-valuetext="clockText(position)" @input="seek(position)"></label>
          <div class="flex gap-2 overflow-x-auto pb-2" :aria-label="t('videoWorkspace.shotList')"><button v-for="(shot, index) in draft.shots" :key="shot.id" :aria-pressed="selectedShotId === shot.id" :class="selectedShotId === shot.id ? 'border-accent1' : 'border-border'" class="min-w-36 max-w-48 shrink-0 rounded-lg border p-3 text-left" @click="selectedShotId = shot.id"><strong>{{ index + 1 }} · {{ clockText(shot.start_sec) }}–{{ clockText(shot.start_sec + (shot.seconds ?? 4)) }}</strong><span class="mt-1 block truncate text-xs">{{ shot.prompt }}</span><span v-if="shotProblem(draft.shots, shot.id, project.duration_sec)" class="block text-xs text-status-failed">{{ t('videoWorkspace.needsFix') }}</span></button></div>
          <div class="flex flex-wrap gap-2"><button :disabled="readOnly || draft.shots.length >= 40" @click="addShot">{{ t('video.addShot') }}</button><button :disabled="readOnly || !undoStack.length" @click="undo">{{ t('videoWorkspace.undo') }}</button><label class="inline-check"><input v-model="ripple" type="checkbox">{{ t('videoWorkspace.ripple') }}</label><button :disabled="!canAnalyze" @click="analyze">{{ t('videoWorkspace.reanalyze') }}</button></div>
          <p v-if="readiness && !readiness.analysis_ready" class="text-sm text-status-failed">{{ t('videoWorkspace.analysisDependencyHint') }}</p>
        </div>
        <fieldset v-if="selectedShot" :disabled="readOnly" class="rounded-xl bg-panel p-4 space-y-4">
          <div class="grid gap-3 sm:grid-cols-2"><label>{{ t('video.start') }}<input v-model.number="selectedShot.start_sec" type="number" min="0" :max="project.duration_sec" step="0.041666666666666664" @change="selectedShot.start_sec = frameTime(selectedShot.start_sec)"></label><label>{{ t('videoWorkspace.seed') }}<input v-model.number="selectedShot.seed" type="number" min="0" max="2147483647"></label></div>
          <div><span class="block mb-1 text-sm">{{ t('video.length') }}</span><div role="group" :aria-label="t('video.length')" class="flex flex-wrap gap-2"><button v-for="seconds in videoClipLengths" :key="seconds" :aria-pressed="selectedShot.seconds === seconds" :class="selectedShot.seconds === seconds ? 'bg-accent1 text-white' : ''" @click="length(seconds)">{{ seconds }} s</button></div></div>
          <label>{{ t('video.prompt') }}<textarea v-model="selectedShot.prompt" :disabled="!generatedMode" data-testid="video-shot-prompt" rows="4" maxlength="2000"></textarea><span class="text-xs text-text-dim">{{ selectedShot.prompt.length }} / 2000 · {{ t('videoWorkspace.promptHint') }}</span></label>
          <div class="grid gap-3 sm:grid-cols-2"><label>{{ t('videoWorkspace.reference') }}<select v-model="selectedShot.reference_id"><option :value="null">{{ generatedMode ? t('videoWorkspace.none') : t('videoWorkspace.firstImage') }}</option><option v-for="image in project.references" :key="image.id" :value="image.id">{{ image.name }}</option></select></label><label>{{ t('videoWorkspace.strength') }}<input v-model.number="selectedShot.reference_strength" :disabled="!generatedMode" type="range" min="0" max="1" step="0.05"><span>{{ selectedShot.reference_strength ?? 0.7 }}</span></label></div>
          <p v-if="!generatedMode" class="text-sm text-text-dim">{{ t('videoWorkspace.imageMotionHint') }}</p>
          <label class="inline-check"><input v-model="selectedShot.locked" type="checkbox">{{ t('videoWorkspace.lock') }}</label>
          <p v-if="shotProblem(draft.shots, selectedShot.id, project.duration_sec)" role="alert" class="text-status-failed">{{ videoErrorText(shotProblem(draft.shots, selectedShot.id, project.duration_sec)) }}</p>
          <div class="flex flex-wrap gap-2"><button @click="editShots(moveShot(draft.shots, selectedShotId, -1))">{{ t('videoWorkspace.moveEarlier') }}</button><button @click="editShots(moveShot(draft.shots, selectedShotId, 1))">{{ t('videoWorkspace.moveLater') }}</button><button :disabled="selectedShot.seconds === 2 || draft.shots.length >= 40" @click="editShots(splitShot(draft.shots, selectedShotId, newVideoId()))">{{ t('videoWorkspace.split') }}</button><button :disabled="draft.shots.length >= 40" @click="editShots(duplicateShot(draft.shots, selectedShotId, newVideoId()))">{{ t('videoWorkspace.duplicate') }}</button><button @click="removeShot">{{ t('video.removeShot') }}</button></div>
        </fieldset>
        <div class="rounded-xl bg-panel p-4 space-y-2"><div class="flex flex-wrap items-center justify-between gap-2"><h3>{{ t('videoWorkspace.markers') }} <span v-if="project.analysis?.tempo_bpm" class="text-text-dim">~{{ Math.round(project.analysis.tempo_bpm) }} BPM</span></h3><button :disabled="readOnly" @click="addMarker">{{ t('videoWorkspace.addMarker') }}</button></div><p class="text-xs text-text-dim">{{ t('videoWorkspace.markerHint') }}</p><div class="max-h-48 overflow-y-auto space-y-1"><div v-for="marker in importantMarkers" :key="marker.id" class="flex flex-wrap items-center gap-2 text-sm"><button @click="seek(marker.time_sec)">{{ clockText(marker.time_sec) }} · {{ marker.label || marker.kind }}</button><span>{{ Math.round((marker.confidence ?? 0) * 100) }}%</span><button :disabled="readOnly || !selectedShot" @click="snapShot(marker)">{{ t('videoWorkspace.snap') }}</button><button v-if="marker.kind === 'manual'" :disabled="readOnly" @click="draft.markers = draft.markers.filter((item) => item.id !== marker.id)">{{ t('video.removeShot') }}</button></div></div></div>
        <div class="flex flex-wrap gap-2"><button :disabled="!canCompute || !selectedShot" class="primary" @click="preview()">{{ t('videoWorkspace.previewShot') }}</button><button @click="step = 'preview'">{{ t('videoWorkspace.reviewPreviews') }}</button></div>
        <p class="text-sm text-text-dim">{{ t('videoWorkspace.previewHint') }}</p>
      </template>
      <template v-else-if="draft && project && step === 'preview'">
        <h2 class="text-lg font-semibold">{{ t('videoWorkspace.previewGoal') }}</h2>
        <fieldset :disabled="readOnly" class="rounded-xl bg-panel p-4 space-y-3"><label>{{ t('videoWorkspace.variantCount') }}<select v-model.number="variantsPerShot"><option :value="1">1</option><option :value="2">2</option><option :value="3" :disabled="!generatedMode">3</option></select></label><p v-if="!generatedMode" class="text-sm text-text-dim">{{ t('videoWorkspace.imageMotionHint') }}</p><div class="flex flex-wrap gap-3"><label v-for="(shot, index) in draft.shots" :key="shot.id" class="inline-check"><input v-model="selectedPreviewIds" type="checkbox" :value="shot.id">{{ index + 1 }} · {{ clockText(shot.start_sec) }}</label></div><button :disabled="!canCompute || !selectedPreviewIds.length" class="primary" @click="preview(selectedPreviewIds)">{{ t('videoWorkspace.previewSelected') }}</button></fieldset>
        <div class="flex gap-2 overflow-x-auto"><button v-for="(shot, index) in draft.shots" :key="shot.id" :aria-pressed="selectedShotId === shot.id" @click="selectedShotId = shot.id">{{ t('video.shotLabel', { current: index + 1 }) }} <span v-if="project.shots?.find((item) => item.id === shot.id)?.approved_variant_id">✓</span></button></div>
        <p v-if="!shotVariants.length" class="text-text-dim">{{ t('videoWorkspace.noVariants') }}</p>
        <div class="grid gap-4 md:grid-cols-2"><article v-for="variant in shotVariants" :key="variant.id" class="rounded-xl border bg-panel p-4 space-y-3" :class="savedShot?.approved_variant_id === variant.id ? 'border-accent1' : 'border-border'">
          <div class="flex items-center justify-between gap-2"><strong>{{ t('videoWorkspace.seed') }} {{ variant.seed }}</strong><span>{{ t(`videoWorkspace.status.${variant.status ?? 'queued'}`) }}</span></div>
          <VideoPreviewPlayer v-if="variant.status === 'ready' && variant.file_url" :src="variant.file_url" :poster="variant.poster_url" :label="t('videoWorkspace.variantVideo', { seed: variant.seed })" />
          <p v-if="variant.error_code" class="text-status-failed">{{ videoErrorText(variant.error_code) }}</p>
          <details><summary>{{ t('videoWorkspace.details') }}</summary><p class="mt-2 whitespace-pre-wrap text-sm">{{ variant.prompt || selectedShot?.prompt }}</p><p class="text-xs text-text-dim">{{ variant.settings?.engine_pack }} · {{ variant.settings?.width }}×{{ variant.settings?.height }} · {{ variant.settings?.stage1_steps }} + {{ variant.settings?.stage2_steps }} · CFG {{ variant.settings?.cfg_scale }}</p><p v-for="timing in variant.timings" :key="timing.started_at" class="text-xs">{{ timing.phase }} · {{ clockText(timing.duration_sec ?? 0) }}</p></details>
          <button v-if="variant.status === 'ready'" :disabled="readOnly || savedShot?.approved_variant_id === variant.id" @click="approve(selectedShotId, variant.id)">{{ savedShot?.approved_variant_id === variant.id ? t('videoWorkspace.approved') : t('videoWorkspace.approve') }}</button>
        </article></div>
        <div class="flex flex-wrap gap-2"><button :disabled="!canCompute || !selectedShot" @click="preview()">{{ t('videoWorkspace.retryShot') }}</button><button @click="step = 'storyboard'">{{ t('videoWorkspace.editStoryboard') }}</button><button class="primary" @click="step = 'export'">{{ t('videoWorkspace.continueExport') }}</button></div>
      </template>
      <template v-else-if="draft && project && step === 'export'">
        <h2 class="text-lg font-semibold">{{ t('videoWorkspace.exportGoal') }}</h2><p class="text-sm text-text-dim">{{ t('videoWorkspace.reuseHint', { approved: approvalCount, total: draft.shots.length }) }}</p>
        <fieldset :disabled="readOnly" class="rounded-xl bg-panel p-4 space-y-4"><div class="grid gap-4 sm:grid-cols-2"><label>{{ t('videoWorkspace.aspect') }}<select v-model="draft.export_settings.aspect"><option value="landscape">16:9</option><option value="portrait">9:16</option><option value="square">1:1</option></select></label><label>{{ t('videoWorkspace.encodeQuality') }}<select v-model="draft.export_settings.quality"><option value="fast">{{ t('video.qualityFaster') }}</option><option value="standard">{{ t('video.qualityStandard') }}</option><option value="high">{{ t('videoWorkspace.high') }}</option></select></label></div><p class="text-xs text-text-dim">{{ t('videoWorkspace.exportHint') }}</p>
          <label class="inline-check"><input v-model="draft.export_settings.include_overlays" type="checkbox">{{ t('videoWorkspace.includeText') }}</label><div class="flex justify-between gap-2"><h3>{{ t('videoWorkspace.overlays') }}</h3><button :disabled="draft.overlays.length >= 100" @click="addOverlay">{{ t('videoWorkspace.addText') }}</button></div>
          <div v-for="overlay in draft.overlays" :key="overlay.id" class="space-y-2 rounded-lg bg-panel-2 p-3"><label>{{ t('videoWorkspace.text') }}<textarea v-model="overlay.text" rows="2" maxlength="500"></textarea></label><div class="grid gap-2 sm:grid-cols-4"><label>{{ t('video.start') }}<input v-model.number="overlay.start_sec" type="number" min="0" :max="project.duration_sec" step="0.1"></label><label>{{ t('videoWorkspace.end') }}<input v-model.number="overlay.end_sec" type="number" min="0" :max="project.duration_sec" step="0.1"></label><label>{{ t('videoWorkspace.position') }}<select v-model="overlay.position"><option value="top">{{ t('videoWorkspace.top') }}</option><option value="center">{{ t('videoWorkspace.center') }}</option><option value="bottom">{{ t('videoWorkspace.bottom') }}</option></select></label><label>{{ t('videoWorkspace.textSize') }}<input v-model.number="overlay.font_size" type="number" min="14" max="96"></label></div><label>{{ t('videoWorkspace.color') }}<input v-model="overlay.color" type="color"></label><button @click="draft.overlays = draft.overlays.filter((item) => item.id !== overlay.id)">{{ t('video.removeShot') }}</button></div>
        </fieldset>
        <p v-if="serverBusy" class="text-text-dim">{{ t('video.othersBusy') }}</p><p v-if="problem" class="text-status-failed">{{ videoErrorText(problem) }}</p>
        <p v-if="!textReady" role="status" class="text-sm text-status-failed">{{ t('videoWorkspace.textDependencyHint') }}</p>
        <div class="flex flex-wrap gap-2"><button class="primary" :disabled="!canAssemble" @click="render">{{ t('videoWorkspace.render') }}</button><button :disabled="!canExport || approvalCount !== draft.shots.length" @click="exportVideo">{{ t('videoWorkspace.exportApproved') }}</button><button v-if="project.job?.status === 'failed' || project.job?.status === 'cancelled'" :disabled="!canResume" @click="resume">{{ t('videoWorkspace.resume') }}</button></div>
        <VideoPreviewPlayer v-if="project.file_url" :key="project.file_url" :src="project.file_url" :poster="project.poster_url" :label="t('videoWorkspace.finishedVideo')" /><a v-if="project.file_url" :href="project.file_url" download class="inline-block min-h-11 rounded-lg bg-accent1 px-4 py-3 text-white">{{ t('common.download') }}</a>
      </template>
      <p v-else class="text-text-dim">{{ t('videoWorkspace.chooseSong') }} <button @click="step = 'song'">{{ t('videoWorkspace.song') }}</button></p>
    </section>
    <template v-for="item in videoWorkspaceSteps" :key="item"><div v-if="item !== step" :id="`video-panel-${item}`" role="tabpanel" :aria-labelledby="`video-step-${item}`" hidden></div></template>
    <details v-if="legacyVideos.length" class="rounded-xl bg-panel p-4"><summary>{{ t('videoWorkspace.previousVideos') }} ({{ legacyVideos.length }})</summary><div class="mt-4 grid gap-3 sm:grid-cols-2"><label>{{ t('videoWorkspace.search') }}<input v-model="search" type="search"></label><label>{{ t('videoWorkspace.filter') }}<select v-model="statusFilter"><option value="all">{{ t('videoWorkspace.all') }}</option><option v-for="status in ['ready', 'failed', 'cancelled', 'running', 'queued']" :key="status" :value="status">{{ t(`videoWorkspace.status.${status}`) }}</option></select></label></div><ul class="mt-4 grid gap-4 md:grid-cols-2"><li v-for="row in visibleVideos" :key="row.id" class="rounded-lg bg-panel-2 p-3 space-y-2"><strong>{{ row.title }}</strong> · {{ t(`videoWorkspace.status.${row.status}`) }}<VideoPreviewPlayer v-if="row.status === 'ready' && row.file_url" :src="row.file_url" :label="row.title" /><p v-if="row.error_code" class="text-status-failed">{{ videoErrorText(row.error_code) }}</p><div class="flex gap-2"><a v-if="row.file_url" :href="row.file_url" download>{{ t('common.download') }}</a><button :disabled="isVideoActive(row.status)" @click="removeLegacy(row.id)">{{ t('video.delete') }}</button></div></li></ul><div class="mt-3 flex justify-between"><button :disabled="page <= 1" @click="page--">{{ t('videoWorkspace.previous') }}</button><span>{{ page }} / {{ Math.max(1, Math.ceil(filteredVideos.length / 10)) }}</span><button :disabled="page * 10 >= filteredVideos.length" @click="page++">{{ t('videoWorkspace.next') }}</button></div></details>
  </div>
</template>

<style scoped>
.video-workspace label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
.video-step-status { top: calc(var(--app-header-height, 65px) + .5rem); }
.video-workspace input:not([type=checkbox]):not([type=range]):not([type=color]), .video-workspace select, .video-workspace textarea { width: 100%; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
.video-workspace button { min-height: 44px; padding: .5rem .75rem; border-radius: .5rem; background-color: var(--color-panel-2); }
.video-workspace button.primary, .video-workspace button[aria-selected=true], .video-workspace button[aria-pressed=true] { background: var(--color-accent1); color: white; }
.video-workspace button:disabled { opacity: .45; cursor: not-allowed; }
.video-workspace button:focus-visible, .video-workspace input:focus-visible, .video-workspace select:focus-visible, .video-workspace textarea:focus-visible { outline: 2px solid var(--color-accent1); outline-offset: 2px; }
.video-workspace label.inline-check { flex-direction: row; align-items: center; min-height: 44px; }
.video-workspace input[type=checkbox] { width: 18px; height: 18px; }
@media (max-width: 540px) { .video-workspace [role=tab] { font-size: .7rem; padding-inline: .2rem; } }
</style>
