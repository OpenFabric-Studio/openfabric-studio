import { computed, ref, watch } from 'vue'

export const PAGE_SIZES = [5, 10, 25, 50] as const
export type PageSize = typeof PAGE_SIZES[number]
const STORAGE_KEY = 'openfabric.pageSize'
function isPageSize(size: number): size is PageSize { return PAGE_SIZES.some(value => value === size) }
function storedSize(): PageSize {
  try { const size = Number(localStorage.getItem(STORAGE_KEY)); if (isPageSize(size)) return size } catch { /* Storage can be unavailable. */ }
  return 5
}
export function usePagination<T>(items: () => readonly T[]) {
  const pageSize = ref<PageSize>(storedSize())
  const requestedPage = ref(1)
  const total = computed(() => items().length)
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
  const page = computed(() => Math.min(requestedPage.value, totalPages.value))
  const pageItems = computed(() => items().slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
  const rangeFrom = computed(() => total.value ? (page.value - 1) * pageSize.value + 1 : 0)
  const rangeTo = computed(() => Math.min(total.value, page.value * pageSize.value))
  watch(totalPages, last => { requestedPage.value = Math.min(requestedPage.value, last) }, { flush: 'sync' })
  function setPage(value: number): void { requestedPage.value = Number.isFinite(value) ? Math.min(totalPages.value, Math.max(1, Math.floor(value))) : 1 }
  function resetPage(): void { requestedPage.value = 1 }
  function setPageSize(value: number): void {
    if (!isPageSize(value)) return
    pageSize.value = value; resetPage()
    try { localStorage.setItem(STORAGE_KEY, String(value)) } catch { /* Use this size for the current session. */ }
  }
  const barProps = computed(() => ({ page: page.value, pageSize: pageSize.value, totalPages: totalPages.value, total: total.value, rangeFrom: rangeFrom.value, rangeTo: rangeTo.value }))
  return { pageSize, page, totalPages, total, pageItems, rangeFrom, rangeTo, barProps, setPage, setPageSize, resetPage }
}
