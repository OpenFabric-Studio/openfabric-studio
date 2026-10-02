<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { YueNativeProgress } from '../../api/contracts'
const props = defineProps<{ stage?: string; elapsedSeconds?: number; phaseEtaSeconds?: number | null; progress?: YueNativeProgress | null; nativeProgressAvailable?: boolean; stopping?: boolean }>()
const { t, te } = useI18n()
const phase = computed(() => props.progress?.phase ?? props.stage ?? 'queued')
const label = computed(() => te(`generationWorkspace.phases.${phase.value}`) ? t(`generationWorkspace.phases.${phase.value}`) : t('jobStatus.running'))
const percent = computed(() => props.progress?.total ? Math.min(100, props.progress.current / props.progress.total * 100) : null)
</script>
<template>
  <div class="space-y-1 text-xs text-text-dim" role="status">
    <p>{{ stopping ? t('generationWorkspace.stopping') : label }} · {{ t('generationWorkspace.elapsed', { seconds: Math.floor(elapsedSeconds ?? 0) }) }}</p>
    <div v-if="!stopping" class="h-2 overflow-hidden rounded-full bg-panel-2" :role="percent === null ? undefined : 'progressbar'" :aria-label="label" :aria-valuenow="percent === null ? undefined : Math.floor(percent)" :aria-valuemin="percent === null ? undefined : 0" :aria-valuemax="percent === null ? undefined : 100">
      <div class="accent-gradient h-full" :class="{ 'w-3/5 animate-pulse': percent === null }" :style="percent === null ? undefined : { width: `${percent}%` }"></div>
    </div>
    <p v-if="!stopping && nativeProgressAvailable === false && !progress" class="text-text-dim">{{ t('generationWorkspace.nativeProgressUnavailable') }}</p>
    <p v-if="progress">{{ progress.current }}<template v-if="progress.total !== null"> / {{ progress.total }}</template> · {{ label }}</p>
    <p v-if="!stopping">{{ phaseEtaSeconds == null ? t('generationWorkspace.etaUnknown') : t('generationWorkspace.phaseEta', { seconds: Math.ceil(phaseEtaSeconds) }) }}</p>
  </div>
</template>
