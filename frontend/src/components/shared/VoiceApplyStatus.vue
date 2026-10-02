<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ProgressBar from './ProgressBar.vue'
import type { VoiceJobProgress } from '../../api/contracts'
import { formatVoiceTime, voiceElapsedSeconds, voicePhaseRemainingSeconds } from '../../views/voice/voiceProgress'

const props = defineProps<{
  phase?: string
  voiceName?: string
  jobProgress?: VoiceJobProgress | null
}>()

const { t, te } = useI18n()
const now = ref(Date.now() / 1000)
const active = computed(() => !props.jobProgress || props.jobProgress.status === 'queued' || props.jobProgress.status === 'running')
const waiting = computed(() => active.value && (props.jobProgress?.status === 'queued' || ['waiting', 'waiting_gpu', 'queued'].includes(props.jobProgress?.phase ?? props.phase ?? '')))
const phaseLine = computed(() => {
  if (!active.value && props.jobProgress?.status) return t(`jobStatus.${props.jobProgress.status}`)
  if (waiting.value) {
    const phase = props.jobProgress?.phase ?? props.phase
    const reason = props.jobProgress?.queue_reason || (phase === 'waiting_gpu' || phase === 'waiting' ? 'gpu_busy' : 'queued')
    const queueKey = `trackAudio.progress.queue.${reason}`
    const label = props.jobProgress?.queue_label
    return `${te(queueKey) ? t(queueKey) : t('trackAudio.progress.queue.gpu_busy')}${label ? `: ${label}` : ''}`
  }
  const key = `trackAudio.progress.phase.${props.jobProgress?.phase ?? props.phase ?? ''}`
  return te(key) ? t(key) : t('voiceClone.applying')
})
const elapsed = computed(() => voiceElapsedSeconds(props.jobProgress, now.value))
const remaining = computed(() => voicePhaseRemainingSeconds(props.jobProgress, now.value))
const count = computed(() => {
  const progress = props.jobProgress
  return !waiting.value && progress?.phase_total && progress.phase_total > 0
    ? { current: Math.min(progress.phase_total, Math.max(0, progress.phase_current ?? 0)), total: progress.phase_total, unit: progress.phase_unit ?? 'tasks' }
    : null
})
const percent = computed(() => count.value ? Math.round(count.value.current / count.value.total * 100) : null)
let clock: ReturnType<typeof setInterval> | undefined
function stopClock(): void { if (clock !== undefined) clearInterval(clock); clock = undefined }
watch(() => [active.value, props.jobProgress?.job_id], () => {
  stopClock()
  now.value = Date.now() / 1000
  if (active.value && props.jobProgress) clock = setInterval(() => { now.value = Date.now() / 1000 }, 1000)
}, { immediate: true })
onBeforeUnmount(stopClock)
</script>

<template>
  <div class="space-y-1" data-voice-apply-progress>
    <p v-if="voiceName" class="text-xs text-text">{{ active ? t('voiceClone.applyingNamed', { name: voiceName }) : voiceName }}</p>
    <p role="status" aria-live="polite" aria-atomic="true" class="text-xs text-text-dim">{{ phaseLine }}</p>
    <div v-if="active" role="progressbar" :aria-label="t('trackAudio.progress.stageProgress')" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="percent ?? undefined"><ProgressBar :value="percent" /></div>
    <p v-if="count && active" class="text-xs text-text-dim">{{ t(`trackAudio.progress.count.${count.unit}`, { current: count.current, total: count.total }) }}</p>
    <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-dim">
      <p v-if="elapsed != null" data-voice-elapsed>{{ t('trackAudio.progress.elapsed', { time: formatVoiceTime(elapsed) }) }}</p>
      <p v-if="active">{{ t('trackAudio.progress.remaining') }}: {{ waiting ? t('trackAudio.progress.waitingEstimate') : remaining == null ? t('trackAudio.progress.measuring') : `≈ ${formatVoiceTime(remaining)}` }}</p>
    </div>
  </div>
</template>
