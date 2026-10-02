import { computed, ref, watch } from 'vue'

export type SortOrder = 'newest' | 'oldest'
export type DatePreset = 'today' | '7d' | '30d' | 'all'

function toDateInputValue(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** Filters a reactive list by createdAt date range and sorts it newest/oldest first. */
export function useDateFilterSort<T extends { createdAt: number }>(items: () => T[]) {
  const sortOrder = ref<SortOrder>('newest')
  const dateFrom = ref('')
  const dateTo = ref('')
  const activePreset = ref<DatePreset | null>(null)

  const isFiltered = computed(() => dateFrom.value !== '' || dateTo.value !== '')

  const filteredSorted = computed(() => {
    let list = items()
    if (dateFrom.value) {
      const fromTs = new Date(`${dateFrom.value}T00:00:00`).getTime()
      list = list.filter((i) => i.createdAt >= fromTs)
    }
    if (dateTo.value) {
      const toTs = new Date(`${dateTo.value}T23:59:59.999`).getTime()
      list = list.filter((i) => i.createdAt <= toTs)
    }
    return [...list].sort((a, b) => (sortOrder.value === 'newest' ? b.createdAt - a.createdAt : a.createdAt - b.createdAt))
  })

  // Clear active preset if user manually changes dates
  watch([dateFrom, dateTo], () => {
    // We only clear it if we are not in the middle of applyPreset
  })

  function applyPreset(preset: DatePreset) {
    activePreset.value = preset
    if (preset === 'all') {
      dateFrom.value = ''
      dateTo.value = ''
      return
    }
    const now = new Date()
    dateTo.value = toDateInputValue(now)
    const from = new Date(now)
    if (preset === '7d') from.setDate(from.getDate() - 6)
    else if (preset === '30d') from.setDate(from.getDate() - 29)
    else if (preset === 'today') from.setDate(from.getDate())
    dateFrom.value = toDateInputValue(from)
  }

  function reset() {
    sortOrder.value = 'newest'
    dateFrom.value = ''
    dateTo.value = ''
    activePreset.value = null
  }

  return { sortOrder, dateFrom, dateTo, activePreset, isFiltered, filteredSorted, applyPreset, reset }
}
