<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAceStepStore } from '../../stores/aceStep'
import { useDateFilterSort } from '../../composables/useDateFilterSort'
import FilterSortBar from '../../components/shared/FilterSortBar.vue'
import PaginationBar from '../../components/shared/PaginationBar.vue'
import { usePagination } from '../../composables/usePagination'
import { useTrackActivity } from '../../composables/useTrackActivity'
import JobCard from './JobCard.vue'
import { useTrackView } from '../../composables/useTrackView'
import { formatTrackCodes } from '../../api/tracks'
import { useTrackFavoritesStore } from '../../stores/trackFavorites'

const store = useAceStepStore()
const { t } = useI18n()
const { view, setView } = useTrackView()
const hasActive = computed(() => store.activeJobs.length > 0)
const cancelError = ref('')
const cancelling = ref(false)
const favorites = useTrackFavoritesStore()
const favoritesOnly = ref(false)

const { sortOrder, dateFrom, dateTo, activePreset, isFiltered, filteredSorted, applyPreset, reset } = useDateFilterSort(() => store.jobs)
const visibleJobs = computed(() => filteredSorted.value.filter((job) => !favoritesOnly.value || job.dbIds.some(favorites.isFavorite)))
function visibleIds(ids: number[]): number[] { return favoritesOnly.value ? ids.filter(favorites.isFavorite) : ids }
const visibleCount = computed(() => visibleJobs.value.reduce((count, job) => count + (job.dbIds.length ? visibleIds(job.dbIds).length : job.audioUrls.length || 1), 0))
const totalCount = computed(() => store.jobs.reduce((count, job) => count + (job.dbIds.length || job.audioUrls.length || 1), 0))
function resetFilters() { favoritesOnly.value = false; reset() }
const playingJobs = ref<Set<string>>(new Set())
const processingJobs = ref<Set<string>>(new Set())
const { activeTrackIds, error: activityError } = useTrackActivity()
function noteActivity(kind: 'playing' | 'processing', id: string, active: boolean): void {
  const target = kind === 'playing' ? playingJobs : processingJobs
  const next = new Set(target.value)
  if (active) next.add(id); else next.delete(id)
  target.value = next
}
function isActive(job: typeof store.jobs[number]): boolean { return job.status === 'queued' || job.status === 'running' || job.voiceApply === 'running' || processingJobs.value.has(job.id) || job.dbIds.some(id => activeTrackIds.value.has(id)) }
const { pageItems, barProps, setPage, setPageSize, resetPage } = usePagination(() => visibleJobs.value.filter(job => !isActive(job)))
watch([sortOrder, dateFrom, dateTo, favoritesOnly], resetPage)
const pageIds = computed(() => new Set(pageItems.value.map(job => job.id)))
// A single keyed list preserves the same player even when a card becomes retained across pages.
const renderedJobs = computed(() => store.jobs.filter(job => pageIds.value.has(job.id) || isActive(job) || playingJobs.value.has(job.id)).sort((a, b) => sortOrder.value === 'newest' ? b.createdAt - a.createdAt : a.createdAt - b.createdAt))

watch(() => store.jobs.flatMap((job) => job.dbIds).join(','), () => { void favorites.refresh() }, { immediate: true })

async function stopAll() {
  if (cancelling.value) return
  cancelError.value = ''
  cancelling.value = true
  try { await store.cancelAll() }
  catch (error) { cancelError.value = error instanceof Error ? error.message : t('storeErrors.unknownError') }
  finally { cancelling.value = false }
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-lg font-semibold text-text">{{ t('feed.yourTracks') }}</h2>
      <div class="flex items-center gap-2">
        <div class="flex rounded-lg border border-border p-0.5 text-xs">
          <button type="button" class="rounded-md px-2 py-1" :class="view === 'list' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('list')">{{ t('feed.viewList') }}</button>
          <button type="button" class="rounded-md px-2 py-1" :class="view === 'cards' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('cards')">{{ t('feed.viewCards') }}</button>
        </div>
        <button v-if="hasActive" type="button" class="rounded-lg border border-status-failed/40 px-3 py-1.5 text-xs text-status-failed hover:bg-status-failed/10 disabled:opacity-50" :disabled="cancelling" @click="stopAll">
          {{ t('feed.stopAll') }}
        </button>
      </div>
    </div>
    <p v-if="cancelError" class="text-sm text-status-failed" role="alert">{{ cancelError }}</p>
    <p v-if="favorites.loading" class="text-xs text-text-dim" role="status">{{ t('trackFavorites.loading') }}</p>
    <p v-if="favorites.loadError" class="text-sm text-status-failed" role="alert">{{ t('trackFavorites.loadFailed') }} <button type="button" class="text-accent1 underline" @click="favorites.refresh()">{{ t('trackFavorites.retry') }}</button></p>

    <div v-if="store.historyError" class="space-y-2 rounded-lg bg-status-failed/10 p-3 text-sm text-status-failed" role="alert">
      <p>{{ store.historyError }}</p>
      <button type="button" class="text-accent1 hover:underline" @click="store.loadHistory()">{{ t('aceJob.retryLoad') }}</button>
    </div>

    <FilterSortBar
      v-if="store.jobs.length > 0"
      v-model:sort-order="sortOrder"
      v-model:date-from="dateFrom"
      v-model:date-to="dateTo"
      v-model:favorites-only="favoritesOnly"
      :favorites-loading="favorites.loading && !favorites.loaded"
      :is-filtered="isFiltered || favoritesOnly"
      :visible-count="visibleCount"
      :total-count="totalCount"
      :active-preset="activePreset"
      @preset="applyPreset"
      @reset="resetFilters"
    />

    <p v-if="activityError" role="status" class="text-xs text-text-dim">{{ t('upstreamLibrary.activityFailed') }}</p>
    <PaginationBar v-bind="barProps" compact @update:page="setPage" @update:page-size="setPageSize" />

    <!-- Skeleton loaders while history is loading -->
    <template v-if="!store.historyLoaded">
      <div v-for="i in 3" :key="'skel-' + i" class="animate-pulse rounded-xl border border-border bg-panel p-4 space-y-3">
        <div class="flex items-center justify-between">
          <div class="h-4 w-1/3 rounded bg-panel-2"></div>
          <div class="h-5 w-16 rounded-full bg-panel-2"></div>
        </div>
        <div class="h-3 w-2/3 rounded bg-panel-2"></div>
        <div class="h-10 w-full rounded-lg bg-panel-2"></div>
      </div>
    </template>

    <p v-else-if="store.jobs.length === 0" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-text-dim">{{ t('feed.emptyHint') }}</p>
    <p v-else-if="visibleJobs.length === 0" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-text-dim">{{ t(favoritesOnly ? 'trackFavorites.noneMatching' : 'feed.noneInPeriod') }}</p>
    <div :class="view === 'list' ? 'divide-y divide-border overflow-hidden rounded-xl border border-border' : 'space-y-3'">
      <div v-for="job in renderedJobs" :key="job.id" :data-library-job="job.id">
        <p v-if="isActive(job)" class="px-3 py-2 text-xs text-accent1" role="status">{{ t('upstreamLibrary.retainedActive') }}</p>
        <p v-else-if="!pageIds.has(job.id) && playingJobs.has(job.id)" class="px-3 py-2 text-xs text-accent1" role="status">{{ t('upstreamLibrary.retainedPlaying') }}</p>
        <JobCard :job="job" :processing-active="processingJobs.has(job.id) || job.dbIds.some(id => activeTrackIds.has(id))" :number="formatTrackCodes(favoritesOnly && !isActive(job) && !playingJobs.has(job.id) ? visibleIds(job.dbIds).map(favorites.trackCode) : job.shortIds)" :view="view" :visible-track-ids="isActive(job) || playingJobs.has(job.id) ? job.dbIds : visibleIds(job.dbIds)" :favorites-only="favoritesOnly" @playing="noteActivity('playing', job.id, $event)" @processing="noteActivity('processing', job.id, $event)" />
      </div>
    </div>
    <PaginationBar v-bind="barProps" @update:page="setPage" @update:page-size="setPageSize" />
  </div>
</template>
