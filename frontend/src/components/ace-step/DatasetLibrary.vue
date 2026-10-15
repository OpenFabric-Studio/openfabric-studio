<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LoraDatasetEntry } from '../../api/generated'

const props = defineProps<{ datasets: LoraDatasetEntry[]; selectedId: string | null; disabled: boolean; loading: boolean; status?: string }>()
const emit = defineEmits<{ select: [id: string]; create: [name: string]; rename: [name: string]; trash: []; restore: [id: string] }>()
const { t } = useI18n()
const mode = ref<'new' | 'rename' | null>(null), name = ref('')
const selected = computed(() => props.datasets.find(item => item.id === props.selectedId && !item.trashed))
const active = computed(() => props.datasets.filter(item => !item.trashed))
const trashed = computed(() => props.datasets.filter(item => item.trashed))
function change(event: Event) {
  if (event.target instanceof HTMLSelectElement && event.target.value) emit('select', event.target.value)
}
function edit(next: 'new' | 'rename') { mode.value = next; name.value = next === 'rename' ? selected.value?.name ?? '' : '' }
function submit() {
  if (props.disabled || !name.value.trim()) return
  if (mode.value === 'new') emit('create', name.value.trim())
  else if (mode.value === 'rename') emit('rename', name.value.trim())
  mode.value = null
}
</script>

<template>
  <section class="space-y-3 rounded-xl border border-border bg-panel p-5" :aria-busy="loading">
    <div class="flex flex-wrap items-end gap-3">
      <label class="min-w-48 flex-1 space-y-1 text-sm text-text-dim">
        <span>{{ t('loraRecovery.savedDataset') }}</span>
        <select :value="selectedId ?? ''" :aria-label="t('loraRecovery.savedDataset')" class="dataset-input" :disabled="disabled || loading" @change="change">
          <option value="" disabled>{{ t('loraRecovery.chooseDataset') }}</option>
          <option v-for="item in active" :key="item.id" :value="item.id" :disabled="!item.available">{{ item.name }}{{ item.available ? '' : ` — ${t('loraRecovery.unavailable')}` }}</option>
        </select>
      </label>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="dataset-button" :disabled="disabled || loading" @click="edit('new')">{{ t('loraRecovery.new') }}</button>
        <button type="button" class="dataset-button" :disabled="disabled || loading || !selected" @click="edit('rename')">{{ t('loraRecovery.rename') }}</button>
        <button type="button" class="dataset-button" :disabled="disabled || loading || !selected" @click="emit('trash')">{{ t('loraRecovery.trash') }}</button>
      </div>
    </div>
    <p v-if="selected" role="status" class="text-xs text-text-dim">{{ t('loraRecovery.counts', { samples: selected.sample_count, labelled: selected.labeled_count }) }}<span v-if="status"> · {{ status }}</span></p>
    <p class="text-xs leading-relaxed text-text-dim">{{ t('loraRecovery.libraryHint') }}</p>
    <form v-if="mode" class="flex flex-wrap items-end gap-2" @submit.prevent="submit">
      <label class="flex-1 space-y-1 text-sm text-text-dim"><span>{{ t('lora.datasetNameLabel') }}</span><input v-model="name" class="dataset-input" maxlength="120" required :disabled="disabled" autofocus /></label>
      <button type="submit" class="dataset-button" :disabled="disabled || !name.trim()">{{ t(mode === 'new' ? 'loraRecovery.create' : 'loraRecovery.saveName') }}</button>
      <button type="button" class="dataset-button" :disabled="disabled" @click="mode = null">{{ t('loraRecovery.cancel') }}</button>
    </form>
    <details v-if="trashed.length" class="space-y-2 text-sm text-text-dim">
      <summary class="cursor-pointer py-2">{{ t('loraRecovery.trashTitle', { count: trashed.length }) }}</summary>
      <ul class="space-y-2"><li v-for="item in trashed" :key="item.id" class="flex items-center justify-between gap-3"><span>{{ item.name }}</span><button type="button" class="dataset-button" :disabled="disabled || loading" @click="emit('restore', item.id)">{{ t('loraRecovery.restore') }}</button></li></ul>
    </details>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.dataset-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
.dataset-button { @apply min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
