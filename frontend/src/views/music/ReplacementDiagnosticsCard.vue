<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { EqBand, PitchRecommendation, ReplacementDiagnostics } from '../../api/generated'

const props = defineProps<{ report: ReplacementDiagnostics; disabled?: boolean }>()
const emit = defineEmits<{ pitch: [value: PitchRecommendation]; eq: [value: EqBand[]] }>()
const { t } = useI18n()
function signed(value: number): string { return `${value > 0 ? '+' : ''}${value.toFixed(1)}` }
function pitch(value: PitchRecommendation) { if (!props.disabled) emit('pitch', value) }
function eq() {
  const bands = props.report.tonal?.matching_eq.suggested_eq_bands
  if (!props.disabled && bands?.length) emit('eq', bands.map(band => ({ ...band })))
}
function vocal(id: string): string {
  return t(props.report.tonal?.artifacts.find(item => item.identity.artifact_id === id)?.role === 'source_vocal'
    ? 'replacement.diagnostics.roles.reference' : 'replacement.diagnostics.roles.candidate')
}
</script>
<template>
  <section class="space-y-4 rounded-xl border border-border bg-panel p-4" :aria-label="t('replacement.diagnostics.title')">
    <h3 class="font-semibold">{{ t('replacement.diagnostics.title') }}</h3>
    <p class="text-sm text-text-dim">{{ t('replacement.diagnostics.help') }}</p>
    <p v-if="report.unavailable?.length" role="status" class="text-sm text-status-warning">{{ t('replacement.diagnostics.unavailable') }}: {{ report.unavailable.join(', ') }}</p>
    <template v-if="report.pitch">
      <h4 class="text-sm font-semibold">{{ t('replacement.diagnostics.pitchTitle') }}</h4>
      <div v-for="artifact in report.pitch.artifacts" :key="artifact.identity.artifact_id" class="space-y-1 text-sm">
        <p class="font-medium">{{ t(`replacement.diagnostics.roles.${artifact.role}`) }}</p>
        <p class="text-text-dim">{{ t('replacement.diagnostics.support', {sampled:artifact.sampled_sec.toFixed(1),reliable:artifact.reliable_voiced_sec.toFixed(1)}) }}</p>
        <p v-if="artifact.status === 'observed'">{{ t('replacement.diagnostics.range', {low:artifact.pitch_p05_hz?.toFixed(0),high:artifact.pitch_p95_hz?.toFixed(0),median:artifact.pitch_median_hz?.toFixed(0)}) }}</p>
        <p v-else class="text-text-dim">{{ t('replacement.diagnostics.unknown') }} · {{ t(`replacement.diagnostics.reasons.${artifact.unknown_reason}`) }}</p>
      </div>
      <article v-for="comparison in report.pitch.comparisons ?? []" :key="comparison.kind" class="space-y-2 rounded-lg border border-border p-3 text-sm">
        <h5 class="font-medium">{{ t(`replacement.diagnostics.comparisons.${comparison.kind}`) }}</h5>
        <p v-if="comparison.median_delta_semitones != null">{{ t('replacement.diagnostics.delta', {delta:signed(comparison.median_delta_semitones),windows:comparison.supported_windows}) }}</p>
        <p v-if="comparison.reason" class="text-text-dim">{{ t(`replacement.diagnostics.reasons.${comparison.reason}`) }}</p>
        <template v-if="comparison.recommendation">
          <p>{{ t('replacement.diagnostics.proposal', {value:signed(comparison.recommendation.pitch_shift_semitones + comparison.recommendation.pitch_nudge_semitones)}) }}</p>
          <button type="button" class="diagnostic-button" :disabled="disabled" @click="pitch(comparison.recommendation)">{{ t('replacement.diagnostics.usePitch') }}</button>
        </template>
      </article>
    </template>
    <template v-if="report.tonal">
      <h4 class="text-sm font-semibold">{{ t('replacement.diagnostics.eqTitle') }}</h4>
      <p class="text-sm text-text-dim">{{ t('replacement.diagnostics.eqHelp') }}</p>
      <p v-if="report.tonal.matching_eq.reason" class="text-sm text-text-dim">{{ t(`replacement.diagnostics.reasons.${report.tonal.matching_eq.reason}`) }}</p>
      <template v-if="report.tonal.matching_eq.status === 'available'">
        <div class="overflow-x-auto"><table class="w-full text-left text-sm"><caption class="sr-only">{{ t('replacement.diagnostics.eqTitle') }}</caption><thead><tr><th scope="col" class="p-2">{{ t('replacement.diagnostics.frequency') }}</th><th scope="col" class="p-2">{{ t('replacement.diagnostics.gain') }}</th></tr></thead><tbody><tr v-for="band in report.tonal.matching_eq.suggested_eq_bands ?? []" :key="band.frequency_hz"><th scope="row" class="p-2 font-normal">{{ band.frequency_hz }} Hz</th><td class="p-2">{{ signed(band.gain_db) }} dB</td></tr></tbody></table></div>
        <p class="text-sm text-text-dim">{{ t('replacement.diagnostics.beforeDifference', {value:report.tonal.matching_eq.before_difference_db?.toFixed(2)}) }}</p>
        <p v-if="report.tonal.matching_eq.after_difference_db != null" class="text-sm text-text-dim">{{ t('replacement.diagnostics.afterDifference', {value:report.tonal.matching_eq.after_difference_db.toFixed(2)}) }}</p>
        <button type="button" class="diagnostic-button" :disabled="disabled || !report.tonal.matching_eq.suggested_eq_bands?.length" @click="eq">{{ t('replacement.diagnostics.useEq') }}</button>
      </template>
      <h4 class="text-sm font-semibold">{{ t('replacement.diagnostics.bleedTitle') }}</h4>
      <p class="text-sm text-text-dim">{{ t('replacement.diagnostics.bleedHelp') }}</p>
      <article v-for="item in report.tonal.bleed" :key="item.vocal_artifact_id" class="space-y-1 rounded-lg border border-border p-3 text-sm">
        <p class="font-medium">{{ vocal(item.vocal_artifact_id) }} · {{ t(`replacement.diagnostics.correlation.${item.correlation_strength}`) }}</p>
        <p v-if="item.median_absolute_correlation != null">{{ t('replacement.diagnostics.correlationValue', {value:item.median_absolute_correlation.toFixed(3),windows:item.windows?.length ?? 0}) }}</p>
        <p v-if="item.reason" class="text-text-dim">{{ t(`replacement.diagnostics.reasons.${item.reason}`) }}</p>
        <details v-if="item.windows?.length"><summary class="min-h-11 cursor-pointer py-2">{{ t('replacement.diagnostics.windowBasis') }}</summary><p v-for="window in item.windows" :key="window.window_index" class="text-xs text-text-dim">{{ window.window_index + 1 }} · {{ window.lag_ms.toFixed(1) }} ms · {{ window.signed_correlation.toFixed(3) }} · {{ t(`replacement.diagnostics.polarity.${window.polarity}`) }}<span v-if="window.lag_ambiguous"> · {{ t('replacement.diagnostics.ambiguousLag') }}</span></p></details>
      </article>
    </template>
    <details><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('replacement.diagnostics.evidence') }}</summary><pre class="max-h-72 overflow-auto whitespace-pre-wrap break-all text-xs text-text-dim">{{ JSON.stringify(report,null,2) }}</pre></details>
  </section>
</template>
<style scoped>
@reference '../../style.css';
.diagnostic-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2 disabled:opacity-50; }
</style>
