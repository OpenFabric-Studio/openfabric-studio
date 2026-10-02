<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { VideoProject, VideoProjectJob } from '../../api/contracts'
import { isVideoActive } from '../../api/videos'
import { formatClock } from '../../composables/voicePace'
import PaginationBar from '../../components/shared/PaginationBar.vue'

const props = defineProps<{ projects: VideoProject[]; selectedId?: string | null; busy: boolean }>()
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
interface DeleteFocus {
  id: string
  index: number
  button: HTMLButtonElement
  owned: boolean
}
let deleteFocus: DeleteFocus | null = null
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
  if (!props.busy) emit('open', id)
}
function noteFocusMove(event: FocusEvent): void {
  if (deleteFocus && event.target !== deleteFocus.button && event.target !== document.body && event.target !== document.documentElement) deleteFocus.owned = false
}
async function restoreDeleteFocus(): Promise<void> {
  const request = deleteFocus
  if (!request) return
  await nextTick()
  if (deleteFocus !== request || props.busy) return
  deleteFocus = null
  const focused = document.activeElement
  if (!request.owned || focused !== request.button && focused !== document.body && focused !== document.documentElement) return
  if (props.projects.some(row => row.id === request.id) && request.button.isConnected && !request.button.disabled) {
    request.button.focus()
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
onBeforeUnmount(() => { deleteFocus = null; document.removeEventListener('focusin', noteFocusMove) })
function deleteProject(row: VideoProject, event: Event): void {
  if (props.busy || isVideoActive(row.job?.status)) return
  if (!window.confirm(t('videoWorkspace.confirmDeleteProject', { name: row.name }))) return
  const current = props.projects.find(project => project.id === row.id)
  if (!props.busy && current && !isVideoActive(current.job?.status)) {
    const button = event.currentTarget
    deleteFocus = button instanceof HTMLButtonElement && document.activeElement === button
      ? { id: row.id, index: Math.max(0, filteredProjects.value.findIndex(item => item.id === row.id)), button, owned: true } : null
    emit('delete', row.id)
    void restoreDeleteFocus()
  }
}
</script>

<template>
  <section ref="library" id="video-project-library" data-video-project-library aria-labelledby="video-project-library-title" :aria-busy="busy" class="space-y-4 rounded-xl border border-border bg-panel p-4">
    <h2 ref="heading" id="video-project-library-title" tabindex="-1" class="text-lg font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1">{{ t('videoWorkspace.projectLibrary') }} ({{ projects.length }})</h2>
    <div class="grid gap-3 sm:grid-cols-2">
      <label>{{ t('videoWorkspace.searchProjects') }}<input v-model="search" type="search"></label>
      <label>{{ t('videoWorkspace.filter') }}<select v-model="statusFilter" data-project-status><option value="all">{{ t('videoWorkspace.all') }}</option><option v-for="status in statuses" :key="status" :value="status">{{ t(`videoWorkspace.status.${status}`) }}</option></select></label>
    </div>
    <p v-if="!visibleProjects.length" role="status" class="text-sm text-text-dim">{{ t(projects.length ? 'videoWorkspace.noMatchingProjects' : 'videoWorkspace.noSavedProjects') }}</p>
    <ul v-else class="grid gap-4 sm:grid-cols-2">
      <li v-for="row in visibleProjects" :key="row.id" :data-video-project="row.id" :aria-current="row.id === selectedId ? 'true' : undefined" class="space-y-2 rounded-lg border bg-panel-2 p-3" :class="row.id === selectedId ? 'border-accent1' : 'border-border'">
        <img v-if="row.poster_url" :src="row.poster_url" :alt="row.name" loading="lazy" class="h-32 w-full rounded object-cover">
        <div class="flex flex-wrap items-start justify-between gap-2"><h3 class="min-w-0 break-words font-semibold">{{ row.name }}</h3><span v-if="row.id === selectedId" class="text-xs text-accent1">{{ t('videoWorkspace.selectedProject') }}</span></div>
        <p class="text-sm text-text-dim">{{ row.track_title }} · {{ formatClock(row.duration_sec) }} · {{ t(`videoWorkspace.status.${row.job?.status ?? 'draft'}`) }} · {{ t('video.shotCount', { count: row.shots?.length ?? 0 }) }}</p>
        <p v-if="isVideoActive(row.job?.status)" :id="`video-project-${row.id}-cancel-hint`" class="text-sm text-text-dim">{{ t('videoWorkspace.cancelBeforeDelete') }}</p>
        <div class="flex flex-wrap gap-2">
          <button type="button" :data-open-project="row.id" :disabled="busy" :aria-label="t('videoWorkspace.openProjectNamed', { name: row.name })" @click="openProject(row.id)">{{ t('videoWorkspace.openProject') }}</button>
          <a v-if="row.file_url" :href="busy ? undefined : row.file_url" :aria-disabled="busy ? 'true' : undefined" :tabindex="busy ? -1 : undefined" :aria-label="t('videoWorkspace.downloadProject', { name: row.name })" download class="inline-flex min-h-11 items-center rounded-lg border border-border px-3 py-2 text-accent1" :class="{ 'opacity-45': busy }">{{ t('common.download') }}</a>
          <button type="button" data-delete-project :disabled="busy || isVideoActive(row.job?.status)" :aria-label="t('videoWorkspace.deleteProjectNamed', { name: row.name })" :aria-describedby="isVideoActive(row.job?.status) ? `video-project-${row.id}-cancel-hint` : undefined" class="text-status-failed" @click="deleteProject(row, $event)">{{ t('videoWorkspace.deleteProject') }}</button>
        </div>
      </li>
    </ul>
    <PaginationBar v-if="totalPages > 1" v-bind="pagination" compact @update:page="setPage" />
  </section>
</template>

<style scoped>
label { display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
input, select { width: 100%; min-height: 44px; padding: .6rem .75rem; background: var(--color-panel-2); border: 1px solid var(--color-border); border-radius: .5rem; }
button { min-height: 44px; padding: .5rem .75rem; border: 1px solid var(--color-border); border-radius: .5rem; background: var(--color-panel); }
button:disabled { opacity: .45; cursor: not-allowed; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--color-accent1); outline-offset: 2px; }
</style>
