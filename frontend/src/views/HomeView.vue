<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from '../stores/orchestrator'
import { MODEL_LABELS, useModelSwitch } from '../composables/useModelSwitch'
import * as projectsApi from '../api/projects'
import type { ProjectSummary } from '../api/projects'
import type { ModelId } from '../types'
import VoiceSelect from '../components/shared/VoiceSelect.vue'
import TrackAudioVersions from '../components/shared/TrackAudioVersions.vue'
import * as tracksApi from '../api/tracks'
import type { SavedTrack } from '../api/contracts'

const orchestrator = useOrchestratorStore()
const { selectModel } = useModelSwitch()
const { t } = useI18n()

const MODEL_IDS: ModelId[] = ['ace_step', 'yue2']
const DESCRIPTION_KEYS: Record<ModelId, string> = {
  ace_step: 'home.descAceStep',
  yue2: 'home.descYue2',
}

const recentProjects = ref<ProjectSummary[]>([])
const recentTracks = ref<SavedTrack[]>([])
const loading = ref(false)
const tracksFailed = ref(false)
const projectsFailed = ref(false)
let active = true
let generation = 0
let request: AbortController | null = null

async function loadRecent() {
  if (loading.value) return
  const token = ++generation
  request?.abort(); request = new AbortController()
  loading.value = true
  const [projects, tracks] = await Promise.allSettled([projectsApi.listProjects(), tracksApi.listTracks(undefined, request.signal)])
  if (!active || token !== generation) return
  projectsFailed.value = projects.status === 'rejected'
  tracksFailed.value = tracks.status === 'rejected'
  if (projects.status === 'fulfilled') recentProjects.value = [...projects.value].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at)).slice(0, 3)
  if (tracks.status === 'fulfilled') recentTracks.value = [...tracks.value].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id).slice(0, 5)
  loading.value = false
}

onMounted(() => { void loadRecent() })
onBeforeUnmount(() => { active = false; ++generation; request?.abort() })

async function onPick(id: ModelId) {
  try {
    await selectModel(id)
  } catch {
    // handled via orchestrator.switchError, shown in the header.
  }
}
</script>

<template>
  <div class="mx-auto max-w-3xl py-10 text-center">
    <h1 class="text-2xl font-semibold text-text">{{ t('home.title') }}</h1>
    <p class="mt-2 text-sm text-text-dim">{{ t('home.subtitle') }}</p>

    <VoiceSelect link class="mx-auto mt-8 max-w-md" />

    <div class="mt-8 grid gap-4 sm:grid-cols-2">
      <button
        v-for="id in MODEL_IDS"
        :key="id"
        type="button"
        class="group relative overflow-hidden rounded-xl border border-border bg-panel p-6 text-left transition-colors hover:border-accent1/60"
        @click="onPick(id)"
      >
        <div class="text-lg font-semibold text-text">{{ MODEL_LABELS[id] }}</div>
        <p class="mt-2 text-sm text-text-dim">{{ t(DESCRIPTION_KEYS[id]) }}</p>
        <span class="mt-4 inline-block text-xs font-medium text-accent1 transition-transform group-hover:translate-x-1">
          {{ orchestrator.statuses[id]?.status === 'running' ? t('home.open') : t('home.start') }} →
        </span>
      </button>
    </div>

    <section class="mt-12 space-y-4 text-left" :aria-label="t('upstreamWorkspace.recentTracks')">
      <h2 class="text-lg font-semibold text-text">{{ t('upstreamWorkspace.recentTracks') }}</h2>
      <p v-if="loading" role="status" class="text-sm text-text-dim">{{ t('common.loading') }}</p>
      <p v-if="tracksFailed || projectsFailed" role="alert" class="text-sm text-status-failed">
        {{ tracksFailed ? t('upstreamWorkspace.recentFailed') : t('upstreamWorkspace.libraryFailed') }}
        <button type="button" :disabled="loading" class="ml-2 text-accent1 underline" @click="loadRecent">{{ t('upstreamWorkspace.retry') }}</button>
      </p>
      <p v-if="!loading && !tracksFailed && !recentTracks.length" class="text-sm text-text-dim">{{ t('upstreamWorkspace.noTracks') }}</p>
      <article v-for="track in recentTracks" :key="track.id" class="space-y-3 rounded-xl border border-border bg-panel p-4">
        <h3 class="text-sm font-semibold text-text"><span v-if="track.short_id" class="mr-2 font-normal tabular-nums text-text-dim">{{ track.short_id }}</span>{{ track.title || t('library.untitled') }}</h3>
        <TrackAudioVersions :track-id="track.id" :fallback-audio-url="track.audio_url" :fallback-filename="track.filename" />
      </article>
    </section>
    <!-- Recent projects -->
    <div v-if="recentProjects.length > 0" class="mt-16 text-left">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-text">{{ t('home.recentProjects') }}</h2>
        <RouterLink to="/editor" class="text-sm text-accent1 hover:underline">{{ t('home.allProjects') }}</RouterLink>
      </div>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <RouterLink
          v-for="proj in recentProjects"
          :key="proj.id"
          :to="`/editor/${proj.id}`"
          class="flex flex-col justify-between rounded-xl border border-border bg-panel-2 p-4 transition-colors hover:border-accent1/60"
        >
          <div class="truncate text-sm font-medium text-text">{{ proj.name }}</div>
          <div class="mt-2 text-[10px] text-text-dim">{{ new Date(proj.updated_at).toLocaleDateString() }}</div>
        </RouterLink>
      </div>
    </div>
    
  </div>
</template>
