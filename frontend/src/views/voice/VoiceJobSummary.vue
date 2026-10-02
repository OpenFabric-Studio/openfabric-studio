<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { VoiceJobProgress, VoicePreparationResponse, VoiceProfileResponse } from '../../api/contracts'
import { isVoiceActive, voiceErrorText } from '../../api/voices'
import { formatVoiceTime, voiceElapsedSeconds, voicePhaseRemainingSeconds } from './voiceProgress'
import type { VoiceReviewState } from './voiceWorkspace'

const props = defineProps<{ voice: VoiceProfileResponse | null; preparation: VoicePreparationResponse | null; review: VoiceReviewState; uploading: boolean }>()
const emit = defineEmits<{ cancel: [kind: 'preparation' | 'coverage' | 'build']; retry: [kind: 'preparation' | 'coverage' | 'build'] }>()
const { t, te } = useI18n()
const now = ref(Date.now() / 1000)
const job = computed(() => {
  const build = props.voice?.job_progress
  const prep = props.preparation?.progress
  const preparationActive = props.preparation?.status === 'queued' || props.preparation?.status === 'running'
  const preparationProblem = props.preparation?.status === 'failed' || props.preparation?.status === 'cancelled'
  const buildActive = isVoiceActive(props.voice?.status)
  const showBuild = buildActive || !preparationActive && !preparationProblem && !!build && (!prep || (build.finished_at ?? build.observed_at) > (prep.finished_at ?? prep.observed_at))
  if (showBuild || !props.preparation && props.voice?.status !== 'idle' && props.voice) {
    const status = props.voice?.status === 'ready' ? 'done' : buildActive ? props.voice?.status === 'queued' ? 'queued' : 'running' : props.voice?.status ?? 'idle'
    return { kind: 'build' as const, status, progress: build, error: props.voice?.error_code ?? '' }
  }
  const kind: 'preparation' | 'coverage' = props.preparation?.error_code === 'source_changed' ? 'preparation' : props.preparation?.operation === 'coverage' || prep?.kind === 'coverage' ? 'coverage' : 'preparation'
  const status = props.preparation?.status === 'done' ? prep?.status ?? 'done' : props.preparation?.status ?? 'idle'
  return { kind, status, progress: prep, error: props.preparation?.error_code ?? '' }
})
const historical = computed(() => job.value.status === 'done' && !props.uploading && !props.review.error && !job.value.error)
const active = computed(() => job.value.status === 'queued' || job.value.status === 'running')
const elapsed = computed(() => voiceElapsedSeconds(job.value.progress, now.value))
const remaining = computed(() => voicePhaseRemainingSeconds(job.value.progress, now.value))
const phase = computed(() => {
  const value = job.value.progress?.phase ?? (job.value.kind === 'build' ? props.voice?.stage : '')
  const key = `voiceClone.job.phase.${value}`
  return value && te(key) ? t(key) : t('voiceClone.job.unknownPhase')
})
const count = computed(() => {
  const progress = job.value.progress
  if (progress?.phase_total) return { current: progress.phase_current ?? 0, total: progress.phase_total, unit: progress.phase_unit ?? 'tasks' }
  if (!progress && job.value.kind === 'build' && props.voice?.stage === 'training' && props.voice.progress_total) return { current: props.voice.progress_current, total: props.voice.progress_total, unit: 'steps' }
  return null
})
const percent = computed(() => count.value ? Math.round(count.value.current / count.value.total * 100) : null)
const canRetry = computed(() => !props.review.action && !props.uploading && (job.value.kind === 'build' ? props.review.canBuild : job.value.kind === 'coverage' ? props.review.canAnalyzeCoverage : props.review.canPrepare))
const retryVisible = computed(() => job.value.status === 'failed' || job.value.status === 'cancelled')
let clock: ReturnType<typeof setInterval> | undefined
function stopClock() { if (clock !== undefined) clearInterval(clock); clock = undefined }
watch(() => [active.value, job.value.progress?.job_id], () => {
  stopClock(); now.value = Date.now() / 1000
  if (active.value && job.value.progress) clock = setInterval(() => { now.value = Date.now() / 1000 }, 1000)
}, { immediate: true })
onBeforeUnmount(stopClock)
function files(progress: VoiceJobProgress): string { return t('voiceClone.job.files', { current: progress.files_completed ?? 0, total: progress.files_total ?? 0 }) }
</script>

<template>
  <section data-voice-job-summary class="space-y-3 rounded-lg border border-border bg-panel-2 p-4" :class="active || retryVisible ? 'sticky z-10' : ''" style="top: calc(var(--app-header-height, 0px) + .5rem)" :aria-label="t('voiceClone.job.title')">
    <component :is="historical ? 'details' : 'div'" :key="`${job.progress?.job_id}-${job.status}`" class="space-y-3">
    <summary v-if="historical" class="cursor-pointer text-sm text-text-dim">{{ t('singingWorkspace.history') }} · {{ t(`voiceClone.job.kind.${job.kind}`) }}</summary>
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="min-w-0 flex-1"><p v-if="voice" class="break-words text-sm text-text-dim">{{ voice.name }}</p><p role="status" class="font-medium text-text">{{ uploading ? t('voiceClone.workspace.uploading') : `${t(`voiceClone.job.kind.${job.kind}`)} · ${t(`voiceClone.review.status.${job.status}`)}` }}</p></div>
      <button v-if="active && !uploading" type="button" :disabled="!!review.action" class="min-h-10 rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="emit('cancel', job.kind)">{{ t('common.cancel') }}</button>
      <button v-else-if="retryVisible" type="button" :disabled="!canRetry" class="min-h-10 rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="emit('retry', job.kind)">{{ t(`voiceClone.job.retry.${job.kind}`) }}</button>
    </div>
    <template v-if="job.status !== 'idle' && !uploading">
      <p class="text-sm text-text"><span v-if="!active">{{ t('voiceClone.job.lastPhase') }}: </span>{{ phase }}<span v-if="job.progress?.current_file" class="ml-2 break-all text-text-dim">· {{ job.progress.current_file }}</span></p>
      <p v-if="job.progress?.files_total" class="text-xs text-text-dim">{{ files(job.progress) }}</p>
      <p v-if="count && active" class="text-sm text-text-dim">{{ t(`voiceClone.job.count.${count.unit}`, { current: count.current, total: count.total }) }}</p>
      <p v-if="job.kind === 'build' && job.status === 'done'" class="text-sm text-text-dim">{{ voice?.trained_steps ? t('voiceClone.review.trainedSteps', { steps: voice.trained_steps }) : t('voiceClone.review.referenceOnly') }}</p>
      <div v-if="active" class="h-2 overflow-hidden rounded-full bg-border" role="progressbar" :aria-label="t('voiceClone.job.phaseProgress')" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="percent ?? undefined"><div class="h-full bg-accent1" :class="percent == null ? 'w-1/3 animate-pulse' : ''" :style="percent == null ? undefined : { width: `${percent}%` }"></div></div>
      <div class="flex flex-wrap gap-x-6 gap-y-2 text-sm text-text-dim">
        <p data-voice-elapsed>{{ t('voiceClone.job.elapsed') }}: <span class="font-mono text-text">{{ elapsed == null ? t('voiceClone.job.unknownTiming') : formatVoiceTime(elapsed) }}</span></p>
        <p v-if="active">{{ t('voiceClone.job.remaining') }}: {{ t('voiceClone.job.estimating') }}</p>
        <p v-if="active">{{ t('voiceClone.job.phaseRemaining') }}: <span class="font-mono text-text">{{ remaining == null ? t('voiceClone.job.estimating') : `≈ ${formatVoiceTime(remaining)}` }}</span></p>
      </div>
      <p v-if="active" class="text-xs text-text-dim">{{ t('voiceClone.job.estimateHint') }}</p>
    </template>
    <p v-else-if="!uploading" class="text-sm text-text-dim">{{ t(voice ? 'voiceClone.job.idle' : 'voiceClone.dropFirst') }}</p>
    </component>
    <p v-if="job.error || review.error" role="alert" class="break-words text-sm text-status-failed">{{ review.error || voiceErrorText(job.error) }}</p>
    <p v-if="job.kind === 'build' && job.status === 'failed' && voice?.usable" class="text-xs text-text-dim">{{ t('voiceClone.previousKept') }}</p>
  </section>
</template>
