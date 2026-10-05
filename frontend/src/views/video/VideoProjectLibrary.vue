<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { VideoProject, VideoProjectJob } from '../../api/contracts'
import { videoMediaUrl } from './videoWorkspace'
import { isVideoActive } from '../../api/videos'
import { formatClock } from '../../composables/voicePace'
import PaginationBar from '../../components/shared/PaginationBar.vue'

const props = withDefaults(defineProps<{ projects: VideoProject[]; selectedId?: string | null; busy: boolean; compact?: boolean }>(), { compact: false })
const emit = defineEmits<{ open: [id: string]; delete: [id: string] }>()
const { t } = useI18n()
type ProjectStatus = VideoProjectJob['status'] | 'draft'
const statuses: ProjectStatus[] = ['draft', 'ready', 'failed', 'cancelled', 'running', 'queued']
const search = ref('')
const statusFilter = ref<ProjectStatus | 'all'>('all')
const page = ref(1)
const pageSize = 10
const library = ref<HTMLElement | null>(null)
const heading = ref<HTMLElement | null>(null)
const pendingDeleteId = ref<string | null>(null)
const actionsBusy = computed(() => props.busy || pendingDeleteId.value !== null)
interface DeleteFocus {
  id: string
  index: number
  button: HTMLButtonElement
  owned: boolean
}
let deleteFocus: DeleteFocus | null = null
let alive = true
const filteredProjects = computed(() => {
  const query = search.value.trim().toLowerCase()
  return props.projects.filter(row => (statusFilter.value === 'all' || (row.job?.status ?? 'draft') === statusFilter.value)
    && `${row.name} ${row.track_title} ${row.mode ?? ''}`.toLowerCase().includes(query))
})
const totalPages = computed(() => Math.max(1, Math.ceil(filteredProjects.value.length / pageSize)))
const visibleProjects = computed(() => filteredProjects.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const pagination = computed(() => ({ page: page.value, pageSize, totalPages: totalPages.value, total: filteredProjects.value.length,
  rangeFrom: filteredProjects.value.length ? (page.value - 1) * pageSize + 1 : 0, rangeTo: Math.min(page.value * pageSize, filteredProjects.value.length) }))
watch([search, statusFilter], () => { page.value = 1 }, { flush: 'sync' })
watch(totalPages, maximum => { page.value = Math.min(page.value, maximum) }, { flush: 'sync' })
function setPage(value: number): void {
  page.value = Number.isFinite(value) ? Math.max(1, Math.min(totalPages.value, Math.floor(value))) : 1
}
function openProject(id: string): void {
  if (!actionsBusy.value) emit('open', id)
}
function noteFocusMove(event: FocusEvent): void {
  if (deleteFocus && event.target !== deleteFocus.button && event.target !== document.body && event.target !== document.documentElement) deleteFocus.owned = false
}
async function restoreDeleteFocus(): Promise<void> {
  const request = deleteFocus
  const pending = pendingDeleteId.value
  await nextTick()
  if (!alive || props.busy || deleteFocus !== request || pendingDeleteId.value !== pending) return
  pendingDeleteId.value = null
  await nextTick()
  if (!alive || deleteFocus !== request || props.busy || !request) return
  deleteFocus = null
  const focused = document.activeElement
  if (!request.owned || focused !== request.button && focused !== document.body && focused !== document.documentElement) return
  if (props.projects.some(row => row.id === request.id) && request.button.isConnected && !request.button.disabled) {
    const details = request.button.closest('details')
    if (details && !details.open) details.querySelector('summary')?.focus()
    else request.button.focus()
    return
  }
  // The old filtered index selects the next row, or the preceding final row
  // after removal and page clamping. Waited DOM refs are now enabled again.
  const nearby = filteredProjects.value[Math.min(request.index, filteredProjects.value.length - 1)]
  const actions = library.value?.querySelectorAll<HTMLButtonElement>('[data-open-project]')
  const target = nearby ? [...actions ?? []].find(button => button.dataset.openProject === nearby.id) : undefined
  const focusTarget = target ?? heading.value
  focusTarget?.focus()
}
watch([() => props.busy, () => props.projects.map(row => row.id).join(',')], () => { void restoreDeleteFocus() })
onMounted(() => { document.addEventListener('focusin', noteFocusMove) })
onBeforeUnmount(() => { alive = false; deleteFocus = null; document.removeEventListener('focusin', noteFocusMove) })
function deleteProject(row: VideoProject, event: Event): void {
  if (actionsBusy.value || isVideoActive(row.job?.status)) return
  if (!window.confirm(t('videoWorkspace.confirmDeleteProject', { name: row.name }))) return
  const current = props.projects.find(project => project.id === row.id)
  if (!actionsBusy.value && current && !isVideoActive(current.job?.status)) {
    const button = event.currentTarget
    deleteFocus = button instanceof HTMLButtonElement && document.activeElement === button
      ? { id: row.id, index: Math.max(0, filteredProjects.value.findIndex(item => item.id === row.id)), button, owned: true } : null
    pendingDeleteId.value = row.id
    emit('delete', row.id)
    void restoreDeleteFocus()
  }
}
</script>

<template>
  <section ref="library" id="video-project-library" data-video-project-library aria-labelledby="video-project-library-title" :aria-busy="actionsBusy" class="project-library" :class="{ 'is-compact': compact }">
    <h2 ref="heading" id="video-project-library-title" tabindex="-1" class="library-heading">{{ t('videoLibrary.title') }} <span class="library-count">{{ projects.length }}</span></h2>
    <div class="library-filters">
      <label>{{ t('videoWorkspace.searchProjects') }}<input v-model="search" type="search" autocomplete="off"></label>
      <label>{{ t('videoWorkspace.filter') }}<select v-model="statusFilter" data-project-status><option value="all">{{ t('videoWorkspace.all') }}</option><option v-for="status in statuses" :key="status" :value="status">{{ t(`videoWorkspace.status.${status}`) }}</option></select></label>
    </div>
    <p v-if="!visibleProjects.length" role="status" class="text-sm text-text-dim">{{ t(projects.length ? 'videoWorkspace.noMatchingProjects' : 'videoWorkspace.noSavedProjects') }}</p>
    <ul v-else class="project-rows">
      <li v-for="row in visibleProjects" :key="row.id" :data-video-project="row.id" :aria-current="row.id === selectedId ? 'true' : undefined" class="project-row" :class="{ 'is-selected': row.id === selectedId }">
        <button type="button" :data-open-project="row.id" :disabled="actionsBusy" :aria-label="t('videoLibrary.openNamed', { name: row.name })" :aria-current="row.id === selectedId ? 'true' : undefined" :aria-describedby="`video-project-${row.id}-source video-project-${row.id}-status`" class="project-open" @click="openProject(row.id)">
          <img v-if="row.poster_url" :src="videoMediaUrl(row.poster_url, row.output_version)" alt="" loading="lazy" width="48" height="56" class="project-thumbnail">
          <span class="project-copy">
            <span class="project-name">{{ row.name }}</span>
            <span :id="`video-project-${row.id}-source`" class="project-source">{{ row.track_title }} · {{ formatClock(row.duration_sec) }}</span>
            <span :id="`video-project-${row.id}-status`" class="project-badges">
              <span class="project-status" :class="`status-${row.job?.status ?? 'draft'}`">{{ t(`videoWorkspace.status.${row.job?.status ?? 'draft'}`) }}</span>
              <span v-if="row.id === selectedId" class="selected-badge">{{ t('videoWorkspace.selectedProject') }}</span>
              <span v-if="row.source_changed" class="source-changed">{{ t('videoLibrary.sourceChanged') }}</span>
            </span>
          </span>
        </button>
        <details class="project-actions">
          <summary :aria-label="t('videoLibrary.projectActionsNamed', { name: row.name })">{{ t('videoLibrary.projectActions') }}<span aria-hidden="true" class="disclosure-arrow">⌄</span></summary>
          <div class="secondary-actions">
            <p v-if="isVideoActive(row.job?.status)" :id="`video-project-${row.id}-cancel-hint`" class="text-sm text-text-dim">{{ t('videoWorkspace.cancelBeforeDelete') }}</p>
            <a v-if="row.file_url" :href="actionsBusy ? undefined : videoMediaUrl(row.file_url, row.output_version)" :aria-disabled="actionsBusy ? 'true' : undefined" :tabindex="actionsBusy ? -1 : undefined" :aria-label="t('videoWorkspace.downloadProject', { name: row.name })" download :class="{ 'is-disabled': actionsBusy }">{{ t('common.download') }}</a>
            <button type="button" data-delete-project :disabled="actionsBusy || isVideoActive(row.job?.status)" :aria-label="t('videoWorkspace.deleteProjectNamed', { name: row.name })" :aria-describedby="isVideoActive(row.job?.status) ? `video-project-${row.id}-cancel-hint` : undefined" class="text-status-failed" @click="deleteProject(row, $event)">{{ t('videoWorkspace.deleteProject') }}</button>
          </div>
        </details>
      </li>
    </ul>
    <PaginationBar v-if="totalPages > 1" v-bind="pagination" compact class="library-pagination" @update:page="setPage" />
  </section>
</template>

<style scoped>
.project-library { min-width: 0; display: grid; gap: 1rem; padding: 1rem; border: 1px solid var(--color-border); border-radius: .85rem; background: var(--color-panel); }
.library-heading { display: flex; align-items: center; gap: .6rem; font-size: 1rem; font-weight: 650; }
.library-count { padding: .1rem .45rem; border-radius: .4rem; font-size: .75rem; font-weight: 500; color: var(--color-text-dim); background: var(--color-panel-2); }
.library-filters { display: grid; gap: .75rem; }
label { min-width: 0; display: flex; flex-direction: column; gap: .35rem; font-size: .75rem; color: var(--color-text-dim); }
input, select { min-width: 0; width: 100%; min-height: 44px; padding: .6rem .75rem; color: var(--color-text); background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
.project-rows { display: grid; gap: .65rem; }
.project-row { min-width: 0; overflow-wrap: anywhere; border: 1px solid var(--color-border); border-radius: .65rem; background: var(--color-panel-2); }
.project-row.is-selected { border-color: var(--color-accent1); }
button, .secondary-actions a { min-height: 44px; padding: .5rem .75rem; border: 1px solid var(--color-border); border-radius: .5rem; background: var(--color-panel); }
.project-open { display: flex; align-items: flex-start; gap: .65rem; width: 100%; padding: .8rem; border: 0; border-radius: .65rem .65rem 0 0; background: transparent; text-align: left; }
.project-open:hover:not(:disabled) { background: var(--color-panel); }
.project-thumbnail { flex: 0 0 48px; width: 48px; height: 56px; object-fit: cover; border-radius: .35rem; }
.project-copy { min-width: 0; display: grid; gap: .4rem; }
.project-name { font-size: .875rem; font-weight: 600; line-height: 1.4; }
.project-source { font-size: .75rem; line-height: 1.5; color: var(--color-text-dim); }
.project-badges { display: flex; align-items: center; flex-wrap: wrap; gap: .45rem .65rem; font-size: .7rem; }
.project-status { color: var(--color-text-dim); }
.status-ready { color: var(--color-status-done); }
.status-running { color: color-mix(in srgb, var(--color-accent2) 70%, var(--color-text)); }
.status-queued { color: var(--color-status-queued); }
.status-failed, .source-changed { color: var(--color-status-failed); }
.selected-badge { color: color-mix(in srgb, var(--color-accent2) 70%, var(--color-text)); }
.project-actions { border-top: 1px solid var(--color-border); }
summary { min-height: 44px; display: flex; align-items: center; justify-content: space-between; gap: .5rem; padding: .5rem .8rem; font-size: .75rem; color: var(--color-text-dim); cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
.disclosure-arrow { transition: transform .15s ease; }
details[open] .disclosure-arrow { transform: rotate(180deg); }
.secondary-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; padding: 0 .8rem .8rem; }
.secondary-actions p { width: 100%; line-height: 1.5; }
.secondary-actions a { display: inline-flex; align-items: center; color: var(--color-accent1); }
button:disabled, .is-disabled { opacity: .45; cursor: not-allowed; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible, .library-heading:focus-visible { outline: 2px solid var(--color-accent1); outline-offset: 2px; }
.library-pagination :deep(button) { min-width: 44px; min-height: 44px; }
.library-pagination :deep(div) { min-width: 0; width: 100%; margin-left: 0; flex-wrap: wrap; justify-content: center; }
@media (min-width: 640px) { .project-library:not(.is-compact) .library-filters { grid-template-columns: 1fr 1fr; } }
@media (prefers-reduced-motion: reduce) { .disclosure-arrow { transition: none; } }
</style>
