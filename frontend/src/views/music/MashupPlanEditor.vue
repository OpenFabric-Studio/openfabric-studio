<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { parseMashupPlacement } from '../../api/generated'
import type { MashupPlan, MashupPlacement } from '../../api/generated'
const props = defineProps<{ plan: MashupPlan; disabled?: boolean }>()
const emit = defineEmits<{ save: [value: MashupPlacement[]]; dirty: [value: boolean] }>()
const { t } = useI18n(), placements = ref<MashupPlacement[]>([])
let saved = ''
function reset() { placements.value = props.plan.placements.map(item => ({ ...item })); saved = JSON.stringify(placements.value) }
watch(() => `${props.plan.id}:${props.plan.revision}`, reset, { immediate: true })
const dirty = computed(() => JSON.stringify(placements.value) !== saved)
watch(dirty, value => emit('dirty', value), { immediate: true })
const valid = computed(() => { try { return placements.value.every(item => { parseMashupPlacement(item); return item.target_end_tick > item.target_start_tick && item.source_end_frame > item.source_start_frame && item.target_end_tick <= props.plan.duration_ticks }) } catch { return false } })
function locked(id: string) { return props.plan.placements.find(item => item.id === id)?.locked === true }
function save() { if (valid.value && dirty.value && !props.disabled) emit('save', placements.value.map(item => parseMashupPlacement({ ...item }))) }
function percent(tick: number) { return `${Math.max(0, Math.min(100, tick / props.plan.duration_ticks * 100))}%` }
</script>
<template>
  <form class="space-y-4 rounded-xl border border-border bg-panel p-4" @submit.prevent="save">
    <h3 class="font-semibold text-text">{{ t('mashups.placements.title') }}</h3><p class="text-sm leading-relaxed text-text-dim">{{ t('mashups.placements.help') }}</p>
    <fieldset v-for="item in placements" :key="item.id" class="min-w-0 space-y-3 rounded-lg border border-border p-3" :disabled="disabled">
      <legend class="px-2 text-sm font-medium text-text">{{ t('mashups.placements.source', { side: item.source.toUpperCase(), stem: item.stem, section: item.section_id }) }}</legend>
      <div aria-hidden="true" class="relative h-4 overflow-hidden rounded bg-panel-2"><div class="absolute inset-y-0 rounded bg-accent1" :style="{ left: percent(item.target_start_tick), width: percent(item.target_end_tick - item.target_start_tick) }" /></div>
      <p class="text-xs text-text-dim">{{ t('mashups.placements.summary', { start: item.source_start_frame, end: item.source_end_frame }) }}</p>
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <label class="mashup-label">{{ t('mashups.placements.start') }}<input v-model.number="item.target_start_tick" data-start type="number" min="0" :max="plan.duration_ticks" step="1" class="mashup-input" :disabled="disabled || locked(item.id)" /></label>
        <label class="mashup-label">{{ t('mashups.placements.end') }}<input v-model.number="item.target_end_tick" type="number" min="1" :max="plan.duration_ticks" step="1" class="mashup-input" :disabled="disabled || locked(item.id)" /></label>
        <label class="mashup-label">{{ t('mashups.placements.pitch') }}<input v-model.number="item.pitch_semitones" type="number" min="-12" max="12" step="0.1" class="mashup-input" :disabled="disabled || locked(item.id)" /></label>
        <label class="mashup-label">{{ t('mashups.placements.gain') }}<input v-model.number="item.gain_db" type="number" min="-12.0412" max="12.0412" step="0.1" class="mashup-input" :disabled="disabled || locked(item.id)" /></label>
        <label class="mashup-label">{{ t('mashups.placements.fadeIn') }}<input v-model.number="item.fade_in_sec" type="number" min="0" max="10" step="0.01" class="mashup-input" :disabled="disabled || locked(item.id)" /></label>
        <label class="mashup-label">{{ t('mashups.placements.fadeOut') }}<input v-model.number="item.fade_out_sec" type="number" min="0" max="10" step="0.01" class="mashup-input" :disabled="disabled || locked(item.id)" /></label>
      </div>
      <label class="flex min-h-11 items-center gap-3 text-sm text-text"><input v-model="item.locked" type="checkbox" class="size-5 accent-accent1" :disabled="disabled" />{{ t('mashups.placements.locked') }}</label>
      <p v-if="locked(item.id) && !item.locked" class="text-xs text-text-dim">{{ t('mashups.placements.unlock') }}</p>
    </fieldset>
    <p v-if="dirty" role="status" class="text-sm text-text-dim">{{ t('mashups.placements.dirty') }}</p>
    <p v-if="!valid" role="alert" class="text-sm text-status-failed">{{ t('mashups.placements.invalid') }}</p>
    <div class="flex flex-wrap gap-3"><button type="submit" class="mashup-button bg-accent1 text-white" :disabled="disabled || !dirty || !valid">{{ t('mashups.placements.save') }}</button><button type="button" class="mashup-button" :disabled="disabled || !dirty" @click="reset">{{ t('mashups.placements.reset') }}</button></div>
  </form>
</template>
<style scoped>
@reference "../../style.css";
.mashup-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.mashup-label { @apply flex min-w-0 flex-col gap-2 text-sm text-text-dim; }
.mashup-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
