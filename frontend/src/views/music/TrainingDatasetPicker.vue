<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { listLoraDatasets, readSavedLoraDataset } from '../../api/loraDataset'
import type { LoraDatasetEntry, LoraDatasetOperationResponse } from '../../api/generated'
const props = defineProps<{ disabled?: boolean; limit: number }>()
const emit = defineEmits<{ pick: [snapshot: LoraDatasetOperationResponse] }>()
const { t } = useI18n()
const datasets = ref<LoraDatasetEntry[]>([]), selected = ref(''), open = ref(false), busy = ref(false), error = ref('')
const chosen = computed(() => datasets.value.find(dataset => dataset.id === selected.value))
let alive = true, generation = 0, controller: AbortController | undefined
async function action(work: (signal: AbortSignal, current: () => boolean) => Promise<void>) {
  if (!alive || busy.value || props.disabled) return
  const request = new AbortController(), token = ++generation
  const current = () => alive && token === generation && !request.signal.aborted
  controller = request; busy.value = true; error.value = ''
  try { await work(request.signal, current) }
  catch { if (current()) error.value = t('musicLora.dataset.error') }
  finally { if (current()) { busy.value = false; controller = undefined } }
}
function load() {
  open.value = true
  return action(async (signal, current) => {
    const response = await listLoraDatasets(signal)
    if (current()) { datasets.value = response.datasets.filter(dataset => dataset.available && !dataset.trashed && dataset.labeled_count > 0); selected.value = datasets.value[0]?.id ?? '' }
  })
}
function add() {
  const entry = chosen.value
  if (!entry || props.limit < 1) return
  return action(async (signal, current) => {
    const response = await readSavedLoraDataset(entry.id, entry.revision, signal)
    if (current()) { emit('pick', response); open.value = false }
  })
}
onBeforeUnmount(() => { alive = false; ++generation; controller?.abort() })
</script>
<template>
  <div class="space-y-3">
    <button v-if="!open" type="button" class="dataset-button" :disabled="disabled || limit < 1" @click="load">{{ t('musicLora.dataset.choose') }}</button>
    <div v-else class="space-y-3 rounded-lg border border-border p-3" :aria-busy="busy">
      <label class="flex flex-col gap-2 text-sm text-text-dim">{{ t('musicLora.dataset.label') }}<select v-model="selected" name="saved-training-dataset" class="min-h-11 rounded-lg border border-border bg-panel-2 px-3 py-2 text-text" :disabled="busy || disabled"><option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }} · {{ dataset.labeled_count }}</option></select></label>
      <p v-if="!busy && !datasets.length" class="text-sm text-text-dim">{{ t('musicLora.dataset.empty') }}</p>
      <p class="text-xs text-text-dim">{{ t('musicLora.dataset.hint') }}</p>
      <button type="button" class="dataset-button" :disabled="busy || disabled || !chosen || limit < 1" @click="add">{{ t('musicLora.dataset.add', { count: Math.min(chosen?.labeled_count ?? 0, limit) }) }}</button>
      <button type="button" class="dataset-button" :disabled="busy || disabled" @click="load">{{ t('musicLora.refresh') }}</button>
    </div>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{ error }}</p>
  </div>
</template>
<style scoped>
@reference "../../style.css";
.dataset-button { @apply min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
