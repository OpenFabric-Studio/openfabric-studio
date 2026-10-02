<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { PAGE_SIZES } from '../../composables/usePagination'
const { t } = useI18n()
const props = defineProps<{ page: number; pageSize: number; totalPages: number; total: number; rangeFrom: number; rangeTo: number; compact?: boolean }>()
const emit = defineEmits<{ 'update:page': [value: number]; 'update:pageSize': [value: number] }>()
const pages = computed(() => {
 const result: (number | null)[] = []
 const selected = new Set([1, props.totalPages])
 for (let page = props.page - 1; page <= props.page + 1; page++) if (page >= 1 && page <= props.totalPages) selected.add(page)
 const ordered = [...selected].sort((a, b) => a - b)
 for (const page of ordered) { const previous = result[result.length - 1]; if (typeof previous === 'number' && page > previous + 1) result.push(page === previous + 2 ? previous + 1 : null); result.push(page) }
 return result
})
function sizeChanged(event: Event): void { if (event.target instanceof HTMLSelectElement) emit('update:pageSize', Number(event.target.value)) }
</script>
<template>
 <nav v-if="total > PAGE_SIZES[0]" data-pagination :aria-label="t('upstreamLibrary.paginationLabel')" class="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-panel-2/50 px-3 py-2 text-xs">
  <span class="text-text-dim">{{ t('upstreamLibrary.range', { from: rangeFrom, to: rangeTo, total }) }}</span>
  <div class="ml-auto flex items-center gap-1">
   <button data-pagination-prev type="button" :disabled="page <= 1" :aria-label="t('upstreamLibrary.previousPage')" class="min-h-9 min-w-9 rounded-md border border-border disabled:opacity-40 hover:bg-panel" @click="emit('update:page', page - 1)">‹</button>
   <template v-for="(item, index) in pages" :key="item ?? 'gap-' + index">
    <span v-if="item === null" aria-hidden="true" class="px-1">…</span>
    <button v-else type="button" :aria-current="item === page ? 'page' : undefined" :aria-label="t('upstreamLibrary.page', { page: item })" class="min-h-9 min-w-9 rounded-md border px-2" :class="item === page ? 'border-accent1 text-accent1 bg-accent1/10' : 'border-border hover:bg-panel'" @click="emit('update:page', item)">{{ item }}</button>
   </template>
   <button data-pagination-next type="button" :disabled="page >= totalPages" :aria-label="t('upstreamLibrary.nextPage')" class="min-h-9 min-w-9 rounded-md border border-border disabled:opacity-40 hover:bg-panel" @click="emit('update:page', page + 1)">›</button>
  </div>
  <label v-if="!compact" class="flex items-center gap-2 text-text-dim">{{ t('upstreamLibrary.pageSize') }}<select data-pagination-size :value="pageSize" class="min-h-9 rounded-md border border-border bg-panel-2 px-2 text-text" @change="sizeChanged"><option v-for="size in PAGE_SIZES" :key="size" :value="size">{{ size }}</option></select></label>
 </nav>
</template>
