<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { compileBeatPreset, listBeatPresets } from '../../api/beats'
import type { BeatDocument, BeatPresetCatalog, BeatPresetInfo } from '../../api/generated'

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ candidate: [document: BeatDocument] }>()
const { t } = useI18n()
const open = ref(false), busy = ref(false), error = ref(''), search = ref(''), region = ref(''), category = ref('')
const catalog = ref<BeatPresetCatalog | null>(null), selected = ref<string | null>(null), kitId = ref(''), seed = ref(4471), tonic = ref(60)
let active = true, operation = 0, controller: AbortController | null = null
const shown = computed(() => catalog.value?.presets.filter(item => (!region.value || item.preset.region === region.value) && (!category.value || item.preset.category === category.value) && `${item.preset.title} ${item.preset.tradition} ${item.preset.prompt_tags.join(' ')}`.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())) ?? [])
const chosen = computed(() => catalog.value?.presets.find(item => item.preset.id === selected.value))
async function load() {
  if (busy.value) return
  controller?.abort(); const request = new AbortController(); controller = request
  const token = ++operation; busy.value = true; error.value = ''
  try { const result = await listBeatPresets(request.signal); if (active && open.value && token === operation) { catalog.value = result; selected.value = null; kitId.value = '' } }
  catch { if (active && token === operation) error.value = t('beatPresets.loadFailed') }
  finally { if (active && token === operation) { busy.value = false; controller = null } }
}
function toggle() { open.value = !open.value; if (open.value) void load(); else { operation++; controller?.abort(); busy.value = false } }
function select(item: BeatPresetInfo) { selected.value = item.preset.id; kitId.value = item.compatible_kit_ids?.[0] ?? '' }
async function create() {
  if (props.disabled || busy.value || !chosen.value || chosen.value.availability !== 'available' || !chosen.value.compatible_kit_ids?.includes(kitId.value)) return
  const request = { preset_id: chosen.value.preset.id, kit_id: kitId.value, document_id: crypto.randomUUID().replaceAll('-', ''), seed: seed.value, tonic_midi: tonic.value }
  controller?.abort(); const cancellation = new AbortController(); controller = cancellation
  const token = ++operation; busy.value = true; error.value = ''
  try { const result = await compileBeatPreset(request, cancellation.signal); if (active && open.value && token === operation && !props.disabled) emit('candidate', result.document) }
  catch { if (active && token === operation) error.value = t('beatPresets.createFailed') }
  finally { if (active && token === operation) { busy.value = false; controller = null } }
}
onBeforeUnmount(() => { active = false; operation++; controller?.abort() })
</script>

<template>
  <section class="rounded-xl border border-border bg-panel p-4">
    <button type="button" class="preset-button font-semibold" :aria-expanded="open" aria-controls="beat-preset-browser" @click="toggle">{{ t('beatPresets.title') }}</button>
    <div v-if="open" id="beat-preset-browser" class="mt-3 space-y-4" :aria-busy="busy">
      <p class="text-sm text-text-dim">{{ t('beatPresets.intro') }}</p><p class="text-xs text-text-dim">{{ t('beatPresets.importHint') }}</p>
      <p v-if="busy" role="status">{{ t('beatPresets.pending') }}</p><p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
      <button type="button" class="preset-button" :disabled="busy" @click="load">{{ t('beatPresets.refresh') }}</button>
      <div class="grid gap-3 sm:grid-cols-3"><label class="preset-label"><span>{{ t('beatPresets.search') }}</span><input v-model="search" class="preset-input" type="search"></label><label class="preset-label"><span>{{ t('beatPresets.region') }}</span><select v-model="region" class="preset-input"><option value="">{{ t('beatPresets.all') }}</option><option v-for="value in ['china','japan','korea','indonesia','india','thailand']" :key="value" :value="value">{{ t(`beatPresets.regions.${value}`) }}</option></select></label><label class="preset-label"><span>{{ t('beatPresets.category') }}</span><select v-model="category" class="preset-input"><option value="">{{ t('beatPresets.all') }}</option><option v-for="value in ['traditional','fusion','modern_pop']" :key="value" :value="value">{{ t(`beatPresets.categories.${value}`) }}</option></select></label></div>
      <p v-if="catalog && !shown.length" class="text-sm text-text-dim">{{ t('beatPresets.empty') }}</p>
      <div class="grid max-h-[32rem] gap-3 overflow-y-auto lg:grid-cols-2"><article v-for="item in shown" :key="item.preset.id" class="space-y-2 rounded-lg border p-3" :class="selected === item.preset.id ? 'border-accent2' : 'border-border'">
        <h3 class="font-semibold">{{ item.preset.title }}</h3><p class="text-xs text-text-dim">{{ t(`beatPresets.regions.${item.preset.region}`) }} · {{ t(`beatPresets.categories.${item.preset.category}`) }} · {{ item.preset.bpm_default }} BPM · {{ item.preset.meter.numerator }}/{{ item.preset.meter.denominator }}</p>
        <p class="text-xs text-status-warning">{{ t('beatPresets.unreviewed') }} · {{ t(`beatPresets.availability.${item.availability}`) }}</p>
        <p v-if="item.missing_instrument_ids?.length" class="text-xs text-text-dim">{{ t('beatPresets.missing', { instruments: item.missing_instrument_ids.join(', ') }) }}</p>
        <details><summary class="min-h-11 cursor-pointer py-2 text-xs">{{ t('beatPresets.details') }}</summary><p class="text-sm text-text-dim">{{ item.preset.notes }}</p><p v-for="reason in item.reasons ?? []" :key="reason" class="mt-2 text-xs text-status-warning">{{ t(`beatPresets.availability.${reason}`) }}</p></details>
        <button type="button" class="preset-button" :aria-pressed="selected === item.preset.id" :disabled="props.disabled || busy || item.availability !== 'available'" @click="select(item)">{{ item.preset.title }}</button>
      </article></div>
      <fieldset v-if="chosen && chosen.availability === 'available'" :disabled="busy || props.disabled" class="space-y-3 rounded-lg border border-accent2/50 p-3"><legend class="px-2 text-sm font-semibold">{{ chosen.preset.title }}</legend><div class="grid gap-3 sm:grid-cols-3"><label class="preset-label"><span>{{ t('beatPresets.kit') }}</span><select v-model="kitId" class="preset-input"><option v-for="id in chosen.compatible_kit_ids ?? []" :key="id" :value="id">{{ id }}</option></select></label><label class="preset-label"><span>{{ t('beatPresets.seed') }}</span><input v-model.number="seed" type="number" min="0" max="2147483647" class="preset-input"></label><label class="preset-label"><span>{{ t('beatPresets.tonic') }}</span><input v-model.number="tonic" type="number" min="24" max="96" class="preset-input"></label></div><p class="text-xs text-text-dim">{{ t('beatPresets.provenance') }}</p><button type="button" class="preset-button" :disabled="!kitId" @click="create">{{ t('beatPresets.create') }}</button></fieldset>
    </div>
  </section>
</template>

<style scoped>
@reference '../../style.css';
.preset-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent2 disabled:cursor-not-allowed disabled:opacity-50; }
.preset-label { @apply flex min-w-0 flex-col gap-1.5 text-xs text-text-dim; }
.preset-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent2; }
</style>
