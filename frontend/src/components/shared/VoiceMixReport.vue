<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { VoiceMixReport } from '../../api/generated'
import AudioQualityReport from './AudioQualityReport.vue'
import SpeechAudioPreview from '../../views/voice/SpeechAudioPreview.vue'
defineProps<{ report?: VoiceMixReport | null; requested?: boolean; completed?: boolean }>()
const { t } = useI18n()
function measurement(value?: number | null) { return value == null ? t('voiceMix.unknown') : value.toFixed(1) }
</script>
<template>
  <details v-if="report" class="space-y-3 rounded-lg border border-border p-3"><summary class="min-h-11 cursor-pointer py-2 text-sm font-semibold">{{ t('voiceMix.report') }}</summary><div class="space-y-3">
    <div class="grid gap-2 text-sm sm:grid-cols-2"><p>{{ t('voiceMix.original',{value:measurement(report.reference_metrics.integrated_lufs)}) }}</p><p>{{ t('voiceMix.converted',{value:measurement(report.converted_metrics.integrated_lufs)}) }}</p><p>{{ t('voiceMix.gainApplied',{gain:report.applied_gain_db.toFixed(2)}) }}</p><p>{{ report.residual_lu == null ? t('voiceMix.residualUnknown') : t('voiceMix.residual',{value:report.residual_lu.toFixed(1)}) }}</p></div>
    <AudioQualityReport :metrics="report.mix.output_metrics" :target-result="report.mix.target_result" />
    <p v-for="warning in report.warnings ?? []" :key="warning" class="text-xs text-status-warning">{{ t(`voiceMix.warnings.${warning}`) }}</p>
    <h4 class="text-sm font-semibold">{{ t('voiceMix.artifacts') }}</h4><div v-for="artifact in report.artifacts" :key="artifact.id" class="space-y-1"><p class="text-xs text-text-dim">{{ t(`voiceMix.roles.${artifact.role}`) }}</p><SpeechAudioPreview :label="t(`voiceMix.roles.${artifact.role}`)" :src="artifact.audio_url" preload="none" /></div>
    <details><summary class="min-h-11 cursor-pointer py-2 text-xs">{{ t('voiceMix.details') }}</summary><p class="break-all text-xs text-text-dim">{{ t('voiceMix.model',{revision:report.conversion.model_revision_id}) }} · {{ report.source_sha256 }}</p></details>
  </div></details>
  <p v-else-if="requested" role="status" class="text-xs text-text-dim">{{ t(completed ? 'voiceMix.unavailable' : 'voiceMix.requested') }}</p>
</template>
