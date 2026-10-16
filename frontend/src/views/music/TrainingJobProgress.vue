<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { MusicLoraTrainingJob, StableLoraTrainingJob } from '../../api/generated'
import { formatClock } from '../../composables/formatCreated'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'

const props = defineProps<{ job: StableLoraTrainingJob | MusicLoraTrainingJob; reconnecting?: boolean }>()
const { t, locale } = useI18n()
const now = ref(Date.now())
const active = computed(() => props.job.status === 'running' || props.job.status === 'queued')
const queued = computed(() => props.job.status === 'queued')
const packaged = computed(() => 'kind' in props.job && props.job.kind === 'nvidia-package')
const progress = computed(() => props.job.progress)
const stage = computed(() => progress.value?.stage ?? ('stage' in props.job ? props.job.stage : 'preparing'))
const step = computed(() => progress.value?.step)
const pair = computed(() => 'yue_pair' in props.job.request ? props.job.request.yue_pair : null)
const pairStages = ['ar', 'nar'] as const
const completedPair = computed(() => {
  if (!pair.value || props.job.status !== 'done' || packaged.value) return false
  const saved = 'adapters' in props.job ? props.job.adapters ?? [] : []
  return pairStages.every(target => saved.some(adapter => adapter.engine === 'yue2' && adapter.target === target))
})
const arOnly = computed(() => 'yue_ar_source_job_id' in props.job.request && !!props.job.request.yue_ar_source_job_id)
const target = computed(() => progress.value && 'target' in progress.value ? progress.value.target : null)
const totalSteps = computed(() => progress.value?.total_steps ?? (target.value === 'ar' && !arOnly.value ? pair.value?.planner_steps ?? 100 : props.job.request.max_steps ?? 1000))
const elapsed = computed(() => {
  const end = active.value ? now.value : Date.parse(props.job.finished_at ?? '')
  const start = Date.parse(props.job.created_at)
  return formatClock(Number.isFinite(end) && Number.isFinite(start) ? Math.max(0, end - start) / 1000 : null)
})
const rate = computed(() => progress.value?.seconds_per_step)
const stale = computed(() => {
  const updated = Date.parse(progress.value?.updated_at ?? '')
  return !Number.isFinite(updated) || updated > now.value + 5000 || now.value - updated > Math.max(120000, (rate.value ?? 0) * 3000)
})
const eta = computed(() => {
  if (!active.value || packaged.value) return '—'
  if (props.reconnecting) return t('musicLora.progress.reconnecting')
  if (queued.value) return t('musicLora.progress.waiting')
  if (!progress.value) return t('musicLora.progress.previousReporter')
  if (stage.value !== 'training') return t('musicLora.progress.stageEstimate')
  if (step.value == null || step.value < 2 || rate.value == null || rate.value <= 0) return t('musicLora.progress.measuring')
  if (stale.value) return t('musicLora.progress.stale')
  if (step.value >= totalSteps.value) return t('musicLora.progress.finishing')
  return `~${formatClock(rate.value * (totalSteps.value - step.value))}`
})
function dateText(value: string | null | undefined): string {
  const timestamp = Date.parse(value ?? '')
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString(locale.value) : '—'
}
let timer: ReturnType<typeof setInterval> | undefined
watch(active, running => {
  if (timer !== undefined) { clearInterval(timer); timer = undefined }
  if (running) { now.value = Date.now(); timer = setInterval(() => { now.value = Date.now() }, 1000) }
}, { immediate: true })
onBeforeUnmount(() => { if (timer !== undefined) clearInterval(timer) })
</script>

<template>
  <div data-training-progress class="space-y-3">
    <p data-training-run-summary class="text-xs leading-relaxed text-text-dim">{{ t('engine' in job.request ? `musicLora.engines.${job.request.engine}` : `musicLora.targets.${job.request.variant ?? 'small-music'}`) }} · {{ t('musicLora.rank') }} {{ job.request.rank ?? 16 }} · {{ dateText(job.created_at) }}<template v-if="pair"> · {{ t('musicLora.progress.pairLabel') }}</template><template v-if="arOnly"> · {{ t('musicLora.missingAr.runLabel') }}</template></p>
    <div v-if="!packaged" class="space-y-2">
      <div class="flex flex-wrap items-center justify-between gap-2 text-sm">
        <p data-training-stage role="status" class="text-text-dim"><template v-if="completedPair">{{ t('musicLora.progress.pairSaved') }}</template><template v-else>{{ t(`musicLora.modelTraining.stages.${stage ?? 'preparing'}`) }}<template v-if="target"> · {{ t(target === 'ar' ? 'musicLora.modelTraining.planner' : 'musicLora.modelTraining.acousticTarget') }}</template></template></p>
        <p v-if="completedPair" class="font-medium text-text">{{ t('musicLora.progress.pairCount') }}</p>
        <p v-else-if="step != null" class="font-medium tabular-nums text-text">{{ step }} / {{ totalSteps }} <span class="text-text-dim">({{ Math.floor(step / totalSteps * 100) }}%)</span></p>
      </div>
      <progress v-if="completedPair || step != null" :value="completedPair ? 2 : step ?? undefined" :max="completedPair ? 2 : totalSteps" :aria-label="t(completedPair ? 'musicLora.progress.adaptersLabel' : 'musicLora.progress.stepsLabel', { name: job.request.name })" class="h-2 w-full overflow-hidden rounded-full accent-accent1" />
      <div v-if="completedPair" class="grid gap-2 sm:grid-cols-2">
        <div v-for="phase in pairStages" :key="phase" :data-yue-saved="phase" class="rounded-lg border border-border bg-panel-2 p-3 text-sm">
          <p class="font-medium text-text">{{ t(phase === 'ar' ? 'musicLora.modelTraining.planner' : 'musicLora.modelTraining.acousticTarget') }}</p>
          <p class="text-status-done">{{ t('musicLora.progress.savedSteps', { count: phase === 'ar' ? pair?.planner_steps ?? 100 : job.request.max_steps ?? 1000 }) }}</p>
        </div>
      </div>
    </div>
    <dl class="grid gap-3 rounded-lg bg-panel-2 p-3 text-sm sm:grid-cols-3">
      <div><dt class="text-xs text-text-dim">{{ t(queued ? 'musicLora.progress.queued' : 'musicLora.progress.elapsed') }}</dt><dd data-training-elapsed class="mt-1 font-medium tabular-nums text-text">{{ elapsed }}</dd></div>
      <div v-if="!packaged"><dt class="text-xs text-text-dim">{{ t(pair ? 'musicLora.progress.phaseEta' : 'musicLora.progress.eta') }}</dt><dd data-training-eta class="mt-1 font-medium tabular-nums text-text">{{ eta }}</dd></div>
      <div v-if="!packaged"><dt class="text-xs text-text-dim">{{ t('musicLora.modelTraining.loss') }}</dt><dd class="mt-1 font-medium tabular-nums text-text">{{ progress?.loss == null ? '—' : progress.loss.toFixed(4) }}</dd></div>
    </dl>
    <p v-if="!packaged && active" class="text-xs leading-relaxed text-text-dim">{{ t(progress ? pair ? 'musicLora.progress.pairEtaHint' : 'musicLora.progress.etaHint' : 'musicLora.progress.previousReporterHint') }}</p>
    <p v-if="progress?.updated_at || rate" class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-dim">
      <span v-if="rate != null">{{ t('musicLora.progress.speed', { seconds: rate.toFixed(1) }) }}</span>
      <span v-if="progress?.updated_at">{{ t('musicLora.modelTraining.updated') }} <time :datetime="progress.updated_at">{{ dateText(progress.updated_at) }}</time></span>
    </p>
    <CollapsibleDetails :summary="t('musicLora.progress.details')">
      <dl class="grid gap-3 text-sm sm:grid-cols-2">
        <div><dt class="text-xs text-text-dim">{{ t('musicLora.simple.baseModel') }}</dt><dd class="text-text">{{ t('engine' in job.request ? `musicLora.engines.${job.request.engine}` : `musicLora.targets.${job.request.variant ?? 'small-music'}`) }}</dd></div>
        <div><dt class="text-xs text-text-dim">{{ t('musicLora.progress.recordings') }}</dt><dd class="text-text">{{ job.request.samples.length }}</dd></div>
        <div><dt class="text-xs text-text-dim">{{ t('musicLora.progress.submitted') }}</dt><dd class="text-text">{{ dateText(job.created_at) }}</dd></div>
        <div><dt class="text-xs text-text-dim">{{ t('musicLora.progress.started') }}</dt><dd class="text-text">{{ dateText(job.started_at) }}</dd></div>
        <div v-if="!active"><dt class="text-xs text-text-dim">{{ t('musicLora.progress.finished') }}</dt><dd class="text-text">{{ dateText(job.finished_at) }}</dd></div>
        <div v-if="pair"><dt class="text-xs text-text-dim">{{ t('musicLora.modelTraining.planner') }} · {{ t('musicLora.steps') }}</dt><dd class="text-text">{{ pair.planner_steps ?? 100 }}</dd></div>
        <div v-if="pair"><dt class="text-xs text-text-dim">{{ t('musicLora.modelTraining.planner') }} · {{ t('musicLora.learningRate') }}</dt><dd class="text-text">{{ pair.planner_learning_rate ?? 0.00005 }}</dd></div>
        <div><dt class="text-xs text-text-dim"><template v-if="pair">{{ t('musicLora.modelTraining.acousticTarget') }} · </template>{{ t('musicLora.steps') }}</dt><dd class="text-text">{{ job.request.max_steps ?? 1000 }}</dd></div>
        <div><dt class="text-xs text-text-dim">{{ t('musicLora.rank') }}</dt><dd class="text-text">{{ job.request.rank ?? 16 }}</dd></div>
        <div><dt class="text-xs text-text-dim"><template v-if="pair">{{ t('musicLora.modelTraining.acousticTarget') }} · </template>{{ t('musicLora.learningRate') }}</dt><dd class="text-text">{{ job.request.learning_rate ?? 0.0001 }}</dd></div>
        <div><dt class="text-xs text-text-dim">{{ t('musicLora.seed') }}</dt><dd class="text-text">{{ job.request.seed ?? 42 }}</dd></div>
        <div v-if="'segment_seconds' in job.request"><dt class="text-xs text-text-dim">{{ t('musicLora.modelTraining.seconds') }}</dt><dd class="text-text">{{ job.request.segment_seconds ?? 12 }}</dd></div>
        <div class="sm:col-span-2"><dt class="text-xs text-text-dim">{{ t('musicLora.progress.recordings') }}</dt><dd><ul class="space-y-1"><li v-for="(sample, index) in job.request.samples" :key="index" class="break-words text-text">{{ sample.track_id != null ? t('musicLora.progress.trackSource', { id: sample.track_id }) : t('musicLora.progress.datasetSource', { id: sample.dataset?.dataset_id, sample: sample.dataset?.sample_id }) }}</li></ul></dd></div>
      </dl>
    </CollapsibleDetails>
  </div>
</template>
