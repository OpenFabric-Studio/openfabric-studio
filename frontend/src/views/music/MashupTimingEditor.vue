<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { parseMashupTiming } from '../../api/generated'
import type { MashupSourceSnapshot, MashupTiming } from '../../api/generated'
const props = defineProps<{ source: MashupSourceSnapshot; disabled?: boolean }>()
const emit = defineEmits<{ save: [value: MashupTiming]; dirty: [value: boolean] }>()
const { t } = useI18n()
const bars = ref(4), numerator = ref(4), denominator = ref(4), tonic = ref(''), mode = ref<'major' | 'minor'>('major'), confirmed = ref(false)
const sections = ref<{ id: string; bars: number; label: string }[]>([])
const labels = ['intro', 'verse', 'chorus', 'bridge', 'outro', 'instrumental']
let original = '', resetting = false
const fields = computed(() => JSON.stringify([bars.value, numerator.value, denominator.value, tonic.value, mode.value, sections.value]))
watch(() => `${props.source.side}:${props.source.prepared?.sha256}:${JSON.stringify(props.source.timing)}`, () => {
  resetting = true; const saved = props.source.timing
  numerator.value = saved?.meter.numerator ?? 4; denominator.value = saved?.meter.denominator ?? 4
  bars.value = saved ? saved.downbeat_frames.length - 1 : Math.max(1, Math.round((props.source.prepared?.frames ?? 0) / 48000 / 2))
  sections.value = saved ? saved.sections.map(item => ({ id: item.id, bars: item.bars, label: item.musical_label ?? '' })) : [{ id: crypto.randomUUID().replaceAll('-', ''), bars: bars.value, label: '' }]
  tonic.value = saved?.key?.status === 'known' ? String(saved.key.tonic_pc) : ''; mode.value = saved?.key?.mode ?? 'major'
  confirmed.value = false; original = fields.value; resetting = false
}, { immediate: true, flush: 'sync' })
watch(bars, (value, previous) => { if (!resetting && Number.isInteger(value) && Number.isInteger(previous)) { const last = sections.value.at(-1); if (last) last.bars += value - previous } }, { flush: 'sync' })
watch(fields, value => { confirmed.value = false; emit('dirty', value !== original) })
const bpm = computed(() => props.source.prepared?.frames ? bars.value * numerator.value * 4 / denominator.value * 60 * 48000 / props.source.prepared.frames : NaN)
const timing = computed(() => {
  const frames = props.source.prepared?.frames
  if (!frames || !Number.isInteger(bars.value) || bars.value < 1 || bars.value > 4095 || sections.value.reduce((sum, item) => sum + item.bars, 0) !== bars.value) return null
  const boundaries = Array.from({ length: bars.value + 1 }, (_, index) => Math.round(index * frames / bars.value))
  let cursor = 0
  try { return parseMashupTiming({ basis: 'operator_confirmed', bpm: bpm.value, meter: { numerator: numerator.value, denominator: denominator.value }, downbeat_frames: boundaries,
    sections: sections.value.map(item => { const start = cursor; cursor += item.bars; return { id: item.id, start_frame: boundaries[start], end_frame: boundaries[cursor], bars: item.bars, musical_label: item.label || null } }),
    key: tonic.value === '' ? { status: 'unknown' } : { status: 'known', tonic_pc: Number(tonic.value), mode: mode.value, method: 'operator-declared-v1', unknown_reason: null } }) }
  catch { return null }
})
function addSection() { const last = sections.value.at(-1); if (last && last.bars >= 2 && sections.value.length < 64) { last.bars--; sections.value.push({ id: crypto.randomUUID().replaceAll('-', ''), bars: 1, label: '' }) } }
function removeSection(index: number) { const item = sections.value[index], previous = sections.value[index - 1]; if (item && previous) { previous.bars += item.bars; sections.value.splice(index, 1) } }
function save() { if (!props.disabled && timing.value && confirmed.value) emit('save', timing.value) }
</script>

<template>
  <form class="space-y-4 rounded-xl border border-border bg-panel p-4" @submit.prevent="save">
    <h3 class="font-semibold text-text">{{ t('mashups.timing.source', { side: source.side.toUpperCase() }) }}</h3>
    <p class="text-sm leading-relaxed text-text-dim">{{ t('mashups.timing.help') }}</p>
    <div class="grid gap-3 sm:grid-cols-3">
      <label class="mashup-label">{{ t('mashups.timing.bars') }}<input v-model.number="bars" data-bars type="number" min="1" max="4095" step="1" class="mashup-input" :disabled="disabled" /></label>
      <label class="mashup-label">{{ t('mashups.timing.numerator') }}<input v-model.number="numerator" type="number" min="1" max="32" step="1" class="mashup-input" :disabled="disabled" /></label>
      <label class="mashup-label">{{ t('mashups.timing.denominator') }}<select v-model.number="denominator" class="mashup-input" :disabled="disabled"><option v-for="value in [2, 4, 8, 16]" :key="value" :value="value">{{ value }}</option></select></label>
    </div>
    <p class="text-sm text-text-dim">{{ t('mashups.timing.calculated', { bpm: Number.isFinite(bpm) ? bpm.toFixed(3) : '—' }) }}</p>
    <div class="grid gap-3 sm:grid-cols-2">
      <label class="mashup-label">{{ t('mashups.timing.key') }}<select v-model="tonic" class="mashup-input" :disabled="disabled"><option value="">{{ t('mashups.unknown') }}</option><option v-for="(note, index) in ['C', 'C♯', 'D', 'E♭', 'E', 'F', 'F♯', 'G', 'A♭', 'A', 'B♭', 'B']" :key="note" :value="String(index)">{{ note }}</option></select></label>
      <label v-if="tonic !== ''" class="mashup-label">{{ t('mashups.timing.mode') }}<select v-model="mode" class="mashup-input" :disabled="disabled"><option value="major">{{ t('mashups.timing.major') }}</option><option value="minor">{{ t('mashups.timing.minor') }}</option></select></label>
    </div>
    <div v-for="(section, index) in sections" :key="section.id" class="flex flex-wrap items-end gap-3 rounded-lg border border-border p-3">
      <label class="mashup-label flex-1">{{ t('mashups.timing.sectionBars', { number: index + 1 }) }}<input v-model.number="section.bars" type="number" min="1" max="256" step="1" class="mashup-input" :disabled="disabled" /></label>
      <label class="mashup-label flex-1">{{ t('mashups.timing.sectionLabel') }}<select v-model="section.label" class="mashup-input" :disabled="disabled"><option value="">{{ t('mashups.unknown') }}</option><option v-for="label in labels" :key="label" :value="label">{{ t(`mashups.sections.${label}`) }}</option></select></label>
      <button v-if="index > 0" type="button" class="mashup-button" :disabled="disabled" @click="removeSection(index)">{{ t('mashups.timing.merge') }}</button>
    </div>
    <button type="button" class="mashup-button" :disabled="disabled || (sections.at(-1)?.bars ?? 0) < 2 || sections.length >= 64" @click="addSection">{{ t('mashups.timing.split') }}</button>
    <p v-if="!timing" role="alert" class="text-sm text-status-failed">{{ t('mashups.timing.invalid') }}</p>
    <label class="flex min-h-11 items-start gap-3 text-sm text-text"><input v-model="confirmed" type="checkbox" class="mt-1 size-5 shrink-0 accent-accent1" :disabled="disabled || !timing" />{{ t('mashups.timing.confirm', { bars }) }}</label>
    <button type="submit" class="mashup-button bg-accent1 text-white" :disabled="disabled || !timing || !confirmed">{{ t('mashups.timing.save') }}</button>
  </form>
</template>
<style scoped>
@reference "../../style.css";
.mashup-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
.mashup-label { @apply flex min-w-0 flex-col gap-2 text-sm text-text-dim; }
.mashup-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
