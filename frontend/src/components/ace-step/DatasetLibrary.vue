<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LoraDatasetEntry, LoraDatasetIssue } from '../../api/generated'

const props = withDefaults(defineProps<{ datasets: LoraDatasetEntry[]; selectedId: string | null; disabled: boolean; loading: boolean; status?: string; issues?: LoraDatasetIssue[] }>(), { issues: () => [] })
const emit = defineEmits<{ select: [id: string]; create: [name: string]; rename: [name: string]; trash: []; restore: [id: string]; removeRecords: [ids: string[]] }>()
const { t } = useI18n()
const mode = ref<'new' | 'rename' | null>(null), name = ref('')
const selected = computed(() => props.datasets.find(item => item.id === props.selectedId && !item.trashed))
const active = computed(() => props.datasets.filter(item => !item.trashed))
const trashed = computed(() => props.datasets.filter(item => item.trashed))
const removal = ref<Pick<LoraDatasetEntry, 'id' | 'name' | 'revision'>[]>([])
const cancelRemoval = ref<HTMLButtonElement | null>(null)
const removalCurrent = computed(() => removal.value.length > 0 && removal.value.every(record => trashed.value.some(item => item.id === record.id && item.revision === record.revision && item.name === record.name)))
const issueLabels = { dataset_invalid: 'loraRecovery.issueInvalid', dataset_path_unsafe: 'loraRecovery.unsafe', dataset_unavailable: 'loraRecovery.issueUnavailable' } as const
watch(removalCurrent, value => { if (!value) removal.value = [] })
async function askRemoval(records: LoraDatasetEntry[]) {
  if (props.disabled || props.loading) return
  removal.value = records.filter(item => item.trashed).map(({ id, name, revision }) => ({ id, name, revision }))
  await nextTick()
  cancelRemoval.value?.focus()
}
function confirmRemoval() {
  if (props.disabled || props.loading || !removalCurrent.value) return
  emit('removeRecords', removal.value.map(item => item.id))
  removal.value = []
}
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
      <button type="button" class="dataset-button" :disabled="disabled || loading" @click="askRemoval(trashed)">{{ t('loraRecovery.clearTrashRecords') }}</button>
      <ul class="space-y-2"><li v-for="item in trashed" :key="item.id" class="flex flex-wrap items-center justify-between gap-3"><span>{{ item.name }}</span><div class="flex gap-2"><button type="button" class="dataset-button" :disabled="disabled || loading" @click="emit('restore', item.id)">{{ t('loraRecovery.restore') }}</button><button type="button" class="dataset-button" :aria-label="t('loraRecovery.removeRecordNamed', { name: item.name })" :disabled="disabled || loading" @click="askRemoval([item])">{{ t('loraRecovery.removeRecord') }}</button></div></li></ul>
    </details>
    <div v-if="removal.length" role="group" :aria-label="t('loraRecovery.removeRecordsTitle', { count: removal.length }, removal.length)" data-testid="remove-records-confirmation" class="space-y-3 rounded-lg border border-status-queued p-3 text-sm">
      <p class="font-medium">{{ t('loraRecovery.removeRecordsTitle', { count: removal.length }, removal.length) }}</p>
      <ul class="list-inside list-disc"><li v-for="item in removal" :key="item.id">{{ item.name }}</li></ul>
      <p class="text-text-dim">{{ t('loraRecovery.removeRecordsHint') }}</p>
      <div class="flex flex-wrap gap-2"><button ref="cancelRemoval" type="button" class="dataset-button" @click="removal = []">{{ t('loraRecovery.cancel') }}</button><button type="button" class="dataset-button" :disabled="disabled || loading || !removalCurrent" @click="confirmRemoval">{{ t('loraRecovery.confirmRemoveRecords') }}</button></div>
    </div>
    <details v-if="issues.length" class="space-y-2 text-sm text-status-queued">
      <summary class="cursor-pointer py-2">{{ t('loraRecovery.issues', { count: issues.length }) }}</summary>
      <ul class="space-y-2"><li v-for="issue in issues" :key="`${issue.code}:${issue.path}`"><span class="break-all font-medium">{{ issue.path }}</span><span> · {{ t(issueLabels[issue.code]) }}</span></li></ul>
    </details>
  </section>
</template>

<style scoped>
@reference "../../style.css";
.dataset-input { @apply min-h-11 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
.dataset-button { @apply min-h-11 rounded-lg border border-border px-3 py-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-accent1 disabled:opacity-50; }
</style>
