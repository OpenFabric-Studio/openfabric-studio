<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listMusicAdapters } from '../../api/musicLora'
import type { MusicAdapter, MusicAdapterSelection } from '../../api/generated'
const props = withDefaults(defineProps<{ engine: MusicAdapter['engine']; target: MusicAdapter['target']; disabled?: boolean }>(), { disabled: false })
const model = defineModel<MusicAdapterSelection | null>({ default: null })
const emit = defineEmits<{ validity: [valid: boolean] }>()
const { t } = useI18n()
const adapters = ref<MusicAdapter[]>([]), loading = ref(false), failed = ref(false), incomplete = ref(false)
const cleared = ref(false), strength = ref<number | string>(model.value?.strength ?? 1)
const compatible = computed(() => adapters.value.filter(adapter => adapter.engine === props.engine && adapter.target === props.target))
const selected = computed({ get: () => model.value?.id ?? '', set: (id: string) => { cleared.value = false; model.value = compatible.value.some(adapter => adapter.id === id) ? { id, strength: model.value?.id === id ? model.value.strength : 1 } : null } })
const validStrength = computed(() => typeof strength.value === 'number' && Number.isFinite(strength.value) && strength.value >= 0 && strength.value <= 2)
watch(() => model.value === null || validStrength.value, valid => emit('validity', valid), { immediate: true, flush: 'sync' })
watch(strength, value => { if (model.value && typeof value === 'number' && validStrength.value) model.value = { id: model.value.id, strength: value } }, { flush: 'sync' })
watch(model, value => { strength.value = value?.strength ?? 1 })
let alive = true, generation = 0, controller: AbortController | undefined
async function load() {
  const token = ++generation; controller?.abort(); const request = new AbortController(); controller = request
  loading.value = true; failed.value = false
  try {
    const response = await listMusicAdapters(request.signal)
    if (!alive || token !== generation) return
    adapters.value = response.adapters; incomplete.value = response.history_incomplete ?? false
    if (model.value && !compatible.value.some(adapter => adapter.id === model.value?.id)) { model.value = null; cleared.value = true }
  } catch { if (alive && token === generation) failed.value = true }
  finally { if (alive && token === generation) { loading.value = false; controller = undefined } }
}
watch(() => [props.engine, props.target], () => { if (model.value) cleared.value = true; model.value = null; adapters.value = []; incomplete.value = false; void load() })
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort() })
</script>
<template>
  <div class="space-y-2" :aria-busy="loading">
    <div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_9rem]">
      <label class="flex flex-col gap-2 text-sm text-text-dim">{{ t('musicLora.select') }}<select v-model="selected" :name="`adapter-${target}`" :disabled="disabled || loading || failed" class="adapter-input"><option value="">{{ t('musicLora.off') }}</option><option v-for="adapter in compatible" :key="adapter.id" :value="adapter.id">{{ adapter.name }}</option></select></label>
      <label class="flex flex-col gap-2 text-sm text-text-dim">{{ t('musicLora.strength') }}<input v-model.number="strength" type="number" min="0" max="2" step="any" required :aria-invalid="!!model && !validStrength" :disabled="disabled || loading || !model" class="adapter-input" /></label>
    </div>
    <p class="text-xs text-text-dim">{{ t('musicLora.selectorHint') }}</p>
    <p v-if="engine === 'yue2' && target === 'nar'" class="text-xs text-text-dim">{{ t('musicLora.narStrengthHint') }}</p>
    <p v-if="model && !validStrength" role="alert" class="text-sm text-status-failed">{{ t('musicLora.invalidStrength') }}</p>
    <p v-if="cleared" role="status" class="text-xs text-status-queued">{{ t('musicLora.selectionCleared') }}</p>
    <p v-if="failed" role="alert" class="text-sm text-status-failed">{{ t('musicLora.errors.load') }}</p>
    <p v-if="incomplete" role="alert" class="text-xs text-status-queued">{{ t('musicLora.historyIncomplete') }}</p>
    <button type="button" :disabled="disabled || loading" class="min-h-11 text-xs text-accent1 underline focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50" @click="load">{{ t('musicLora.refresh') }}</button>
  </div>
</template>
<style scoped>
@reference "../../style.css";
.adapter-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 p-3 text-text focus-visible:outline-2 focus-visible:outline-accent1; }
</style>
