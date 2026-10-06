<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AudioMetrics, AudioExportResponse } from '../../api/contracts'
defineProps<{ metrics?: AudioMetrics | null; targetResult?: AudioExportResponse['target_result'] | null; measurementUnavailable?: boolean }>()
const { t } = useI18n()
function measured(value?: number | null) { return value === undefined || value === null ? t('exportQuality.unavailable') : value.toFixed(1) }
</script>
<template>
  <div v-if="metrics || measurementUnavailable || targetResult === 'warning' || targetResult === 'inconclusive'" class="space-y-1 rounded-lg border border-border bg-panel-2 p-3 text-xs text-text-dim" data-audio-quality>
    <p class="font-medium text-text">{{ t(metrics ? 'exportQuality.diagnostics' : 'exportQuality.assessment') }}</p>
    <p v-if="metrics">{{ t('exportQuality.metrics', { loudness: measured(metrics.integrated_lufs), truePeak: measured(metrics.true_peak_dbtp), samplePeak: measured(metrics.sample_peak_dbfs) }) }}</p>
    <p v-if="metrics?.full_scale_fraction">{{ t('exportQuality.fullScale', { fraction: (metrics.full_scale_fraction * 100).toFixed(3) }) }}</p>
    <p v-if="!metrics" role="status">{{ t('exportQuality.measurementUnavailable') }}</p>
    <p v-if="targetResult" :class="targetResult === 'warning' || targetResult === 'inconclusive' ? 'text-amber-300' : ''">{{ t(`exportQuality.targetResult.${targetResult}`) }}</p>
  </div>
</template>
