<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { parseVoiceMixSettings } from '../../api/generated'
import type { CompressionSettings, EqBand, VoiceMixSettings } from '../../api/generated'
import LoudnessControls from './LoudnessControls.vue'
const props = defineProps<{ modelValue: VoiceMixSettings | null; disabled?: boolean; alwaysEnabled?: boolean; hideLoudness?: boolean; maxMakeup?: 3 | 6 }>()
const emit = defineEmits<{ 'update:modelValue': [value: VoiceMixSettings | null]; valid: [value: boolean] }>()
const { t } = useI18n()
const invalid = ref<Set<string>>(new Set())
const settings = computed(() => props.modelValue ?? {})
const valid = computed(() => props.modelValue === null && !props.alwaysEnabled || props.modelValue !== null && invalid.value.size === 0 && (settings.value.eq_bands ?? []).reduce((sum, band) => sum + Math.max(0, band.gain_db), 0) <= 6 && (settings.value.compression?.makeup_db ?? 0) <= (props.maxMakeup ?? 6))
watch(valid, value => emit('valid', value), { immediate: true })
function update(patch: Partial<VoiceMixSettings>) { if (!props.disabled) emit('update:modelValue', parseVoiceMixSettings({ ...settings.value, ...patch })) }
function toggle(event: Event) { if (event.target instanceof HTMLInputElement && !props.disabled) { invalid.value = new Set(); emit('update:modelValue', event.target.checked ? { vocal_level: 'match_original', manual_gain_db: 0, eq_bands: [], compression: null, loudness: { profile: 'off' } } : null) } }
function level(event: Event) { if (event.target instanceof HTMLSelectElement) update({ vocal_level: event.target.value === 'manual' ? 'manual' : 'match_original', manual_gain_db: 0 }); invalid.value = new Set([...invalid.value].filter(key => key !== 'gain')) }
function number(event: Event, key: string, min: number, max: number, accept: (value: number) => void) {
  if (!(event.target instanceof HTMLInputElement)) return
  const value = event.target.valueAsNumber, errors = new Set(invalid.value)
  if (!Number.isFinite(value) || value < min || value > max) errors.add(key)
  else { errors.delete(key); accept(value) }
  invalid.value = errors
}
function band(index: number, field: keyof EqBand, value: number) { update({ eq_bands: (settings.value.eq_bands ?? []).map((item, position) => position === index ? { ...item, [field]: value } : item) }) }
function removeBand(index: number) {
  update({ eq_bands: settings.value.eq_bands?.filter((_item, position) => position !== index) })
  invalid.value = new Set([...invalid.value].flatMap(key => {
    const match = /^(freq|q|boost)-(\d+)$/.exec(key)
    if (!match) return [key]
    const position = Number(match[2])
    return position === index ? [] : [`${match[1]}-${position > index ? position - 1 : position}`]
  }))
}
function compress(event: Event) { if (event.target instanceof HTMLInputElement) update({ compression: event.target.checked ? { threshold_dbfs: -18, ratio: 2, knee_db: 6, attack_ms: 10, release_ms: 250, makeup_db: 0 } : null }); invalid.value = new Set([...invalid.value].filter(key => !compressionFields.some(field => field.key === key))) }
function compressor(field: keyof CompressionSettings, value: number) { if (settings.value.compression) update({ compression: { ...settings.value.compression, [field]: value } }) }
const compressionFields = [{key:'threshold_dbfs',label:'threshold',min:-60,max:0,initial:-18},{key:'ratio',label:'ratio',min:1,max:4,initial:2},{key:'knee_db',label:'knee',min:0,max:12,initial:6},{key:'attack_ms',label:'attack',min:1,max:100,initial:10},{key:'release_ms',label:'release',min:10,max:2000,initial:250},{key:'makeup_db',label:'makeup',min:0,max:6,initial:0}] as const
</script>
<template>
  <div class="space-y-3 rounded-lg border border-border p-3">
    <h3 v-if="alwaysEnabled" class="text-sm font-semibold">{{ t('voiceMix.report') }}</h3><label v-else class="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" :checked="modelValue !== null" :disabled="disabled" @change="toggle">{{ t('voiceMix.enable') }}</label>
    <template v-if="modelValue !== null"><p class="text-xs leading-relaxed text-text-dim">{{ t('voiceMix.intro') }}</p>
      <details><summary class="min-h-11 cursor-pointer py-2 text-sm">{{ t('voiceMix.advanced') }}</summary><fieldset :disabled="disabled" class="mt-3 space-y-4">
        <div class="grid gap-3 sm:grid-cols-2"><label class="mix-label"><span>{{ t('voiceMix.level') }}</span><select :value="settings.vocal_level ?? 'match_original'" class="mix-input" @change="level"><option value="match_original">{{ t('voiceMix.match') }}</option><option value="manual">{{ t('voiceMix.manual') }}</option></select></label><label v-if="settings.vocal_level === 'manual'" class="mix-label"><span>{{ t('voiceMix.gain') }}</span><input type="number" :value="settings.manual_gain_db ?? 0" min="-12.0412" max="12.0412" step="0.1" class="mix-input" @input="number($event,'gain',-12.0412,12.0412,value => update({manual_gain_db:value}))"></label></div>
        <div class="space-y-3"><h4 class="text-sm font-semibold">{{ t('voiceMix.eq') }}</h4><p class="text-xs text-text-dim">{{ t('voiceMix.eqHint') }}</p><div v-for="(item,index) in settings.eq_bands ?? []" :key="index" class="grid gap-3 sm:grid-cols-4"><label class="mix-label"><span>{{ t('voiceMix.frequency') }}</span><input type="number" :value="item.frequency_hz" min="20" max="20000" class="mix-input" @input="number($event,`freq-${index}`,20,20000,value => band(index,'frequency_hz',value))"></label><label class="mix-label"><span>{{ t('voiceMix.q') }}</span><input type="number" :value="item.q ?? 1" min="0.1" max="12" step="0.1" class="mix-input" @input="number($event,`q-${index}`,0.1,12,value => band(index,'q',value))"></label><label class="mix-label"><span>{{ t('voiceMix.bandGain') }}</span><input type="number" :value="item.gain_db" min="-6" max="6" step="0.1" class="mix-input" @input="number($event,`boost-${index}`,-6,6,value => band(index,'gain_db',value))"></label><button type="button" class="mix-button self-end" @click="removeBand(index)">{{ t('voiceMix.removeBand') }}</button></div><button type="button" class="mix-button" :disabled="(settings.eq_bands?.length ?? 0) >= 10" @click="update({eq_bands:[...(settings.eq_bands ?? []),{frequency_hz:1000,q:1,gain_db:0}]})">{{ t('voiceMix.addBand') }}</button></div>
        <label class="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" :checked="!!settings.compression" @change="compress">{{ t('voiceMix.compress') }}</label>
        <div v-if="settings.compression" class="grid gap-3 sm:grid-cols-3"><label v-for="field in compressionFields" :key="field.key" class="mix-label"><span>{{ t(`voiceMix.${field.label}`) }}</span><input type="number" :value="settings.compression[field.key] ?? field.initial" :min="field.min" :max="field.key === 'makeup_db' ? (maxMakeup ?? 6) : field.max" step="0.1" class="mix-input" @input="number($event,field.key,field.min,field.key === 'makeup_db' ? (maxMakeup ?? 6) : field.max,value => compressor(field.key,value))"></label></div>
        <LoudnessControls v-if="!hideLoudness" :model-value="settings.loudness ?? {profile:'off'}" :disabled="disabled" @update:model-value="update({loudness:$event})" />
      </fieldset></details><p v-if="!valid" role="alert" class="text-xs text-status-failed">{{ t('voiceMix.invalid') }}</p>
    </template>
  </div>
</template>
<style scoped>
@reference '../../style.css';
.mix-label { @apply flex min-w-0 flex-col gap-1.5 text-xs text-text-dim; }
.mix-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text; }
.mix-button { @apply min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text disabled:opacity-50; }
</style>
