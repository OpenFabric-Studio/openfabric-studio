<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { VoicePreparationState } from './useVoicePreparation'

const props = defineProps<{ state: VoicePreparationState }>()
const { t, te } = useI18n()
const coverage = computed(() => props.state.preparation?.coverage)
const pitchBins = computed(() => {
  const bins = coverage.value?.pitch_bins ?? []
  if (!bins.length) return []
  const first = Math.min(...bins.map((bin) => bin.midi_note))
  const last = Math.max(...bins.map((bin) => bin.midi_note))
  const max = Math.max(...bins.map((bin) => bin.seconds), 0.001)
  return Array.from({ length: last - first + 1 }, (_, index) => {
    const midi = first + index
    const seconds = bins.find((bin) => bin.midi_note === midi)?.seconds ?? 0
    return { midi, seconds, height: seconds / max * 100, name: noteName(midi) }
  })
})
function noteName(midi: number) {
  const notes = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']
  return `${notes[midi % 12]}${Math.floor(midi / 12) - 1}`
}
function hz(value: number | null | undefined) { return value == null ? t('voiceClone.coverage.notMeasured') : `${value.toFixed(0)} Hz` }
function warning(code: string) { const key = `voiceClone.coverage.warning.${code}`; return te(key) ? t(key) : t('voiceClone.review.unknownReason', { code }) }
</script>

<template>
  <section class="space-y-4">
    <div><h3 class="text-base font-semibold text-text">{{ t('voiceClone.coverage.title') }}</h3><p class="mt-1 text-sm text-text-dim">{{ t('voiceClone.coverage.intro') }}</p></div>
    <div class="flex flex-wrap items-center gap-3"><button type="button" :disabled="!state.canAnalyzeCoverage" class="rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50" @click="state.analyzeCoverage">{{ t('voiceClone.coverage.analyze') }}</button><p v-if="state.selectionDirty" class="text-sm text-text-dim">{{ t('voiceClone.coverage.saveFirst') }}</p></div>
    <p v-for="code in (state.preparation?.warnings ?? []).filter((item) => item === 'coverage_cancelled' || item === 'coverage_interrupted')" :key="code" role="status" class="text-sm text-text-dim">{{ state.message(code, 'warning') }}</p>
    <p v-if="!coverage" class="rounded-lg bg-panel-2 p-4 text-sm text-text-dim">{{ t('voiceClone.coverage.unavailable') }}</p>
    <template v-else>
      <p v-if="state.selectionDirty" class="text-sm text-text-dim">{{ t('voiceClone.coverage.savedSelection') }}</p>
      <dl class="grid gap-3 sm:grid-cols-2"><div class="rounded-lg bg-panel-2 p-3"><dt class="text-sm text-text-dim">{{ t('voiceClone.coverage.selectedDuration') }}</dt><dd class="mt-1 text-lg font-semibold text-text">{{ ((state.preparation?.accepted_seconds ?? 0) / 60).toFixed(1) }} {{ t('voiceClone.coverage.minutesUnit') }}</dd></div><div class="rounded-lg bg-panel-2 p-3"><dt class="text-sm text-text-dim">{{ t('voiceClone.coverage.analyzedDuration') }}</dt><dd class="mt-1 text-lg font-semibold text-text">{{ (coverage.analyzed_sec / 60).toFixed(1) }} {{ t('voiceClone.coverage.minutesUnit') }}</dd></div><div class="rounded-lg bg-panel-2 p-3"><dt class="text-sm text-text-dim">{{ t('voiceClone.coverage.voicedDuration') }}</dt><dd class="mt-1 text-lg font-semibold text-text">{{ coverage.voiced_sec.toFixed(1) }} {{ t('common.secondsUnit') }}</dd></div><div class="rounded-lg bg-panel-2 p-3"><dt class="text-sm text-text-dim">{{ t('voiceClone.coverage.reliableDuration') }}</dt><dd class="mt-1 text-lg font-semibold text-text">{{ coverage.reliable_voiced_sec.toFixed(1) }} {{ t('common.secondsUnit') }}</dd></div></dl>
      <p class="text-sm text-text-dim">{{ t('voiceClone.coverage.measurements', { count: coverage.measurements, total: state.preparation?.selected_segment_ids?.length ?? 0, unavailable: coverage.unavailable_segments }) }}</p>
      <figure class="space-y-3 rounded-lg border border-border p-3"><figcaption class="text-sm font-medium text-text">{{ t('voiceClone.coverage.pitchTitle') }}</figcaption><p class="text-sm text-text-dim">{{ t('voiceClone.coverage.pitchSpan', { low: hz(coverage.pitch_p05_hz), median: hz(coverage.pitch_median_hz), high: hz(coverage.pitch_p95_hz) }) }}</p>
        <ol v-if="pitchBins.length" class="flex h-44 items-end gap-1 overflow-x-auto" :aria-label="t('voiceClone.coverage.pitchChart')"><li v-for="bin in pitchBins" :key="bin.midi" class="flex h-full min-w-5 flex-1 flex-col justify-end gap-1" :title="t(bin.seconds > 0 ? 'voiceClone.coverage.observedNote' : 'voiceClone.coverage.unobservedNote', { note: bin.name, seconds: bin.seconds.toFixed(1) })"><span class="sr-only">{{ t(bin.seconds > 0 ? 'voiceClone.coverage.observedNote' : 'voiceClone.coverage.unobservedNote', { note: bin.name, seconds: bin.seconds.toFixed(1) }) }}</span><div aria-hidden="true" class="min-h-1 w-full rounded-t" :class="bin.seconds > 0 ? 'bg-accent1' : 'border border-dashed border-border'" :style="{ height: `${Math.max(1, bin.height)}%` }"></div><span aria-hidden="true" class="shrink-0 text-center text-[10px] text-text-dim">{{ bin.name }}</span></li></ol>
        <p v-else class="text-sm text-text-dim">{{ t('voiceClone.coverage.noPitch') }}</p><p class="text-sm text-text-dim">{{ t('voiceClone.coverage.pitchHint') }}</p>
      </figure>
      <details class="rounded-lg bg-panel-2 p-3 text-sm text-text-dim"><summary class="cursor-pointer font-medium text-text">{{ t('voiceClone.coverage.spectrumTitle') }}</summary><p class="mt-2">{{ t('voiceClone.coverage.spectrumValues', { rolloff: hz(coverage.spectral_rolloff95_hz), fraction: coverage.high_band_energy_fraction == null ? t('voiceClone.coverage.notMeasured') : `${(coverage.high_band_energy_fraction * 100).toFixed(1)}%` }) }}</p><p class="mt-2">{{ t('voiceClone.coverage.spectrumHint') }}</p></details>
      <p v-for="code in coverage.warnings ?? []" :key="code" class="text-sm text-text-dim">{{ warning(code) }}</p>
    </template>
    <details class="rounded-lg bg-panel-2 p-3 text-sm text-text-dim" open><summary class="cursor-pointer font-medium text-text">{{ t('voiceClone.coverage.guidanceTitle') }}</summary><p class="mt-2">{{ t('voiceClone.coverage.guidance') }}</p><p class="mt-2">{{ t('voiceClone.coverage.limits') }}</p></details>
  </section>
</template>
