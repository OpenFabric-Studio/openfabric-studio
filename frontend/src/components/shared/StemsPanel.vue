<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import * as stemsApi from '../../api/stems'
import type { StemsStatus } from '../../api/stems'
import * as projectsApi from '../../api/projects'
import { decodeStem, defaultChannelSettings, defaultMasterSettings } from '../../audio/mixerEngine'
import type { TimelineProject, TimelineLane, Clip } from '../../audio/timelineTypes'
import WaveformPlayer from './WaveformPlayer.vue'
import StemExportControls from './StemExportControls.vue'
import type { SavedTrack } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'

const props = defineProps<{ trackId: number; title: string; lyrics: string; model: SavedTrack['model'] }>()
const { t } = useI18n()

const router = useRouter()
const openingEditor = ref(false)

const STEM_ORDER = ['vocals', 'drums', 'bass', 'other'] as const
const STEM_LABELS = computed<Record<string, string>>(() => ({
  vocals: t('library.stems.vocals'),
  drums: t('library.stems.drums'),
  bass: t('library.stems.bass'),
  other: t('library.stems.other'),
}))

const status = ref<StemsStatus['status']>('idle')
const error = ref<string | null>(null)
const stemUrls = ref<Record<string, string> | null>(null)
const expanded = ref(false)

let alive = true
let generation = 0
let actionGeneration = 0

function applyStatus(s: StemsStatus) {
  status.value = s.status
  error.value = s.error
  stemUrls.value = s.stems
}

const poll = createPollingLoop(async (context) => {
  const trackId = props.trackId
  const session = generation
  try {
    const response = await stemsApi.getSeparationStatus(trackId, context.signal)
    if (!context.isCurrent() || !alive || session !== generation || trackId !== props.trackId) return false
    applyStatus(response)
  } catch {
    // A transient error keeps an active backend job under observation.
  }
  return status.value === 'queued' || status.value === 'running'
}, 2000)

function beginAction() {
  poll.stop()
  const token = ++actionGeneration
  const session = generation
  const trackId = props.trackId
  return { trackId, isCurrent: () => alive && session === generation && token === actionGeneration && trackId === props.trackId }
}

async function start(force = false) {
  const context = beginAction()
  try {
    const response = await stemsApi.startSeparation(context.trackId, force)
    if (!context.isCurrent()) return
    applyStatus(response)
    expanded.value = true
  } catch (e) {
    if (!context.isCurrent()) return
    status.value = 'failed'
    error.value = e instanceof Error ? e.message : String(e)
    return
  }
  poll.start(false)
}

async function cancel() {
  const context = beginAction()
  try {
    const response = await stemsApi.cancelSeparation(context.trackId)
    if (!context.isCurrent()) return
    applyStatus(response)
  } catch (e) {
    if (!context.isCurrent()) return
    error.value = e instanceof Error ? e.message : String(e)
  }
  if (context.isCurrent() && (status.value === 'queued' || status.value === 'running')) poll.start(false)
}

async function removeStems() {
  if (!window.confirm(t('stemsPanel.confirmDelete'))) return
  const context = beginAction()
  await stemsApi.deleteStems(context.trackId)
  if (!context.isCurrent()) return
  status.value = 'idle'
  error.value = null
  stemUrls.value = null
}

function download(name: string, url: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = `${name}_${props.trackId}.wav`
  a.click()
}

async function openInEditor() {
  if (!stemUrls.value) return
  openingEditor.value = true
  error.value = null
  try {
    const lanes: TimelineLane[] = []
    for (const name of STEM_ORDER) {
      if (stemUrls.value[name]) {
        const buffer = await decodeStem(stemUrls.value[name])
        const clip: Clip = {
          id: crypto.randomUUID(),
          sourceUrl: stemUrls.value[name],
          sourceLabel: `${props.title} — ${STEM_LABELS.value[name]}`,
          timelineStart: 0,
          trimStart: 0,
          trimEnd: buffer.duration,
          originalBpm: 120,
        }
        lanes.push({
          id: crypto.randomUUID(),
          name: STEM_LABELS.value[name],
          settings: defaultChannelSettings(),
          clips: [clip],
        })
      }
    }
    const project: TimelineProject = {
      version: 1,
      lanes,
      master: defaultMasterSettings(),
      pxPerSecond: 40,
      bpm: 120,
      snapEnabled: false,
    }
    const created = await projectsApi.createProject(props.title, project)
    await router.push(`/editor/${created.id}`)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    openingEditor.value = false
  }
}

watch(() => props.trackId, () => {
  generation++
  actionGeneration++
  poll.stop()
  applyStatus({ status: 'idle', error: null, stems: null })
  openingEditor.value = false
  poll.start()
})
onMounted(() => poll.start())
onBeforeUnmount(() => {
  alive = false
  generation++
  actionGeneration++
  poll.stop()
})
</script>

<template>
  <div class="rounded-lg border border-border bg-panel-2">
    <button
      type="button"
      class="flex w-full items-center justify-between px-3 py-2 text-xs font-medium text-text-dim"
      @click="expanded = !expanded"
    >
      <span>{{ t('stemsPanel.title') }}</span>
      <span aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
    </button>

    <div v-show="expanded" class="space-y-2 border-t border-border/60 p-3">
      <button
        v-if="status === 'idle' || status === 'failed' || status === 'cancelled'"
        type="button"
        class="accent-gradient rounded-lg px-2.5 py-1 text-xs font-medium text-white"
        @click="start(false)"
      >
        {{ t('stemsPanel.split') }}
      </button>

      <div v-else-if="status === 'queued' || status === 'running'" class="space-y-1">
        <div class="h-2 w-full overflow-hidden rounded-full bg-panel">
          <div class="h-full w-3/5 accent-gradient animate-pulse"></div>
        </div>
        <div class="flex items-center gap-2 text-xs text-text-dim">
          <span>{{ status === 'queued' ? t('stemsPanel.queued') : t('stemsPanel.running') }}</span>
          <button type="button" class="text-text-dim hover:text-status-failed" @click="cancel">{{ t('stemsPanel.cancel') }}</button>
        </div>
      </div>

      <div v-if="status === 'failed' && error" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ error }}</div>
      <div v-if="status === 'cancelled'" class="rounded-lg bg-panel p-2 text-xs text-text-dim">{{ t('stemsPanel.cancelled') }}</div>

      <div v-if="status === 'done' && stemUrls" class="space-y-1.5">
        <div v-for="name in STEM_ORDER.filter((n) => stemUrls?.[n])" :key="name" class="space-y-1 rounded-lg border border-border/60 bg-panel p-2">
          <p class="text-xs text-text-dim">{{ STEM_LABELS[name] }}</p>
          <WaveformPlayer compact :src="stemUrls[name]" />
          <button type="button" class="text-xs text-accent1 hover:underline" @click="download(name, stemUrls![name])">{{ t('stemsPanel.download') }}</button>
          <StemExportControls :track-id="trackId" :stem-name="name" />
        </div>
        <div class="flex gap-2">
          <button type="button" class="accent-gradient rounded-lg px-2.5 py-1 text-xs font-medium text-white" @click="start(true)">{{ t('stemsPanel.recreate') }}</button>
          <button type="button" class="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text" :disabled="openingEditor" @click="openInEditor">
            {{ openingEditor ? '...' : t('mixer.openInEditor') }}
          </button>
          <button type="button" class="rounded-lg border border-status-failed/40 px-2.5 py-1 text-xs font-medium text-status-failed" @click="removeStems">{{ t('stemsPanel.delete') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
