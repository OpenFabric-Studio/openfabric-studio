<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { computed, ref, watch } from 'vue'
import { useYue2Store } from '../../stores/yue2'
import { useDateFilterSort } from '../../composables/useDateFilterSort'
import FilterSortBar from '../../components/shared/FilterSortBar.vue'
import PaginationBar from '../../components/shared/PaginationBar.vue'
import { usePagination } from '../../composables/usePagination'
import { useTrackActivity } from '../../composables/useTrackActivity'
import TrackCard from './TrackCard.vue'
import { useTrackView } from '../../composables/useTrackView'
import { formatTrackCodes } from '../../api/tracks'
import { useTrackFavoritesStore } from '../../stores/trackFavorites'

const store = useYue2Store()
const { t } = useI18n()
const { view, setView } = useTrackView()
const favorites = useTrackFavoritesStore()
const favoritesOnly = ref(false)

const { sortOrder, dateFrom, dateTo, activePreset, isFiltered, filteredSorted, applyPreset, reset } = useDateFilterSort(() => store.jobs)
const visibleJobs = computed(() => filteredSorted.value.filter((job) => !favoritesOnly.value || job.dbId != null && favorites.isFavorite(job.dbId)))
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
function isActive(job: typeof store.jobs[number]): boolean { return job.status === 'queued' || job.status === 'running' || job.status === 'stopping' || job.voiceApply === 'running' || processingJobs.value.has(job.id) || job.dbId != null && activeTrackIds.value.has(job.dbId) }
const { pageItems, barProps, setPage, setPageSize, resetPage } = usePagination(() => visibleJobs.value.filter(job => !isActive(job)))
watch([sortOrder, dateFrom, dateTo, favoritesOnly], resetPage)
const pageIds = computed(() => new Set(pageItems.value.map(job => job.id)))
// A single keyed list preserves the same player even when a card becomes retained across pages.
const renderedJobs = computed(() => store.jobs.filter(job => pageIds.value.has(job.id) || isActive(job) || playingJobs.value.has(job.id)).sort((a, b) => sortOrder.value === 'newest' ? b.createdAt - a.createdAt : a.createdAt - b.createdAt))

watch(() => store.jobs.map((job) => job.dbId).join(','), () => { void favorites.refresh() }, { immediate: true })
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-lg font-semibold text-text">{{ t('feed.yourTracks') }}</h2>
      <div class="flex rounded-lg border border-border p-0.5 text-xs">
        <button type="button" class="rounded-md px-2 py-1" :class="view === 'list' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('list')">{{ t('feed.viewList') }}</button>
        <button type="button" class="rounded-md px-2 py-1" :class="view === 'cards' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('cards')">{{ t('feed.viewCards') }}</button>
      </div>
    </div>
    <p v-if="store.historyError" role="alert" class="text-xs text-status-failed">{{ store.historyError }}</p>
    <p v-if="favorites.loading" class="text-xs text-text-dim" role="status">{{ t('trackFavorites.loading') }}</p>
    <p v-if="favorites.loadError" class="text-sm text-status-failed" role="alert">{{ t('trackFavorites.loadFailed') }} <button type="button" class="text-accent1 underline" @click="favorites.refresh()">{{ t('trackFavorites.retry') }}</button></p>

    <FilterSortBar
      v-if="store.jobs.length > 0"
      v-model:sort-order="sortOrder"
      v-model:date-from="dateFrom"
      v-model:date-to="dateTo"
      v-model:favorites-only="favoritesOnly"
      :favorites-loading="favorites.loading && !favorites.loaded"
      :is-filtered="isFiltered || favoritesOnly"
      :visible-count="visibleJobs.length"
      :total-count="store.jobs.length"
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
        <TrackCard :job="job" :processing-active="processingJobs.has(job.id) || job.dbId != null && activeTrackIds.has(job.dbId)" :number="formatTrackCodes([job.shortId])" :view="view" @playing="noteActivity('playing', job.id, $event)" @processing="noteActivity('processing', job.id, $event)" />
      </div>
    </div>
    <PaginationBar v-bind="barProps" @update:page="setPage" @update:page-size="setPageSize" />
  </div>
</template>
