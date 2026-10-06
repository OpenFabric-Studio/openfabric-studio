<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { LoudnessSettings } from '../../api/contracts'
const props = defineProps<{ modelValue: LoudnessSettings; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Required<LoudnessSettings>] }>()
const { t } = useI18n()
const profiles: NonNullable<LoudnessSettings['profile']>[] = ['off', 'music', 'spoken_word', 'ebu', 'custom']
function complete(): Required<LoudnessSettings> { return { profile: props.modelValue.profile ?? 'off', integrated_lufs: props.modelValue.integrated_lufs ?? -16, true_peak_dbtp: props.modelValue.true_peak_dbtp ?? -2 } }
function profileChanged(event: Event) {
  if (!(event.target instanceof HTMLSelectElement)) return
  const value = event.target.value
  const profile = profiles.find(item => item === value)
  if (profile) emit('update:modelValue', { ...complete(), profile })
}
function numberChanged(event: Event, field: 'integrated_lufs' | 'true_peak_dbtp') {
  if (!(event.target instanceof HTMLInputElement)) return
  const value = event.target.valueAsNumber
  const valid = Number.isFinite(value) && (field === 'integrated_lufs' ? value >= -40 && value <= -8 : value >= -9 && value <= -.1)
  if (valid) emit('update:modelValue', { ...complete(), [field]: value })
}
</script>
<template>
  <fieldset :disabled="disabled" class="space-y-2">
    <label class="block space-y-1 text-xs text-text-dim"><span class="block">{{ t('exportQuality.target') }}</span><select :value="modelValue.profile ?? 'off'" :aria-label="t('exportQuality.target')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text" @change="profileChanged"><option v-for="profile in profiles" :key="profile" :value="profile">{{ t(`exportQuality.profiles.${profile}`) }}</option></select></label>
    <div v-if="modelValue.profile === 'custom'" class="grid gap-2 sm:grid-cols-2">
      <label class="text-xs text-text-dim">{{ t('exportQuality.integrated') }}<input type="number" min="-40" max="-8" step=".1" :value="modelValue.integrated_lufs ?? -16" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-text" @input="numberChanged($event, 'integrated_lufs')"></label>
      <label class="text-xs text-text-dim">{{ t('exportQuality.truePeak') }}<input type="number" min="-9" max="-.1" step=".1" :value="modelValue.true_peak_dbtp ?? -2" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-text" @input="numberChanged($event, 'true_peak_dbtp')"></label>
    </div>
    <p class="text-xs leading-relaxed text-text-dim">{{ t('exportQuality.houseHint') }}</p>
  </fieldset>
</template>
