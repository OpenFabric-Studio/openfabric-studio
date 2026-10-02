<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as midiApi from '../../api/midi'
import type { MidiSource, MidiStatus } from '../../api/midi'
import { drawPianoRoll, parseMidiBytes, playMidiNotes } from '../../audio/miniMidiPlayer'
import type { MidiParsed, MidiSynthHandle } from '../../audio/miniMidiPlayer'
import { createPollingLoop } from '../../composables/polling'

const props = defineProps<{ trackId: number }>()
const { t } = useI18n()

const SOURCE_LABELS = computed<Record<MidiSource, string>>(() => ({
  full: t('midiPanel.sourceFull'),
  vocals: t('library.stems.vocals'),
  drums: t('library.stems.drums'),
  bass: t('library.stems.bass'),
  other: t('library.stems.other'),
}))

const status = ref<MidiStatus | null>(null)
const expanded = ref(false)
const actionError = ref<string | null>(null)

const midiCache = ref<Record<string, MidiParsed>>({})
const playingSource = ref<string | null>(null)
const openRollSource = ref<string | null>(null)
let activeSynth: MidiSynthHandle | null = null

let alive = true
let generation = 0
let playbackGeneration = 0
let rollTimer: ReturnType<typeof setTimeout> | undefined
const actionTokens = new Map<MidiSource, number>()

function isBusy(source: MidiSource): boolean {
  const s = status.value?.sources[source]?.status
  return s === 'queued' || s === 'running'
}

function anyBusy(): boolean {
  return !!status.value && status.value.available.some(isBusy)
}

const poll = createPollingLoop(async (context) => {
  const trackId = props.trackId
  const session = generation
  try {
    const response = await midiApi.getMidiStatus(trackId, context.signal)
    if (!context.isCurrent() || !alive || session !== generation || trackId !== props.trackId) return false
    status.value = response
  } catch {
    // A transient error keeps active backend jobs under observation.
  }
  return anyBusy()
}, 2000)

function beginAction(source: MidiSource) {
  poll.stop()
  const token = (actionTokens.get(source) ?? 0) + 1
  actionTokens.set(source, token)
  const trackId = props.trackId
  const session = generation
  return { trackId, isCurrent: () => alive && session === generation && trackId === props.trackId && actionTokens.get(source) === token }
}

async function start(source: MidiSource, force = false) {
  const context = beginAction(source)
  actionError.value = null
  try {
    const response = await midiApi.startTranscription(context.trackId, source, force)
    if (!context.isCurrent()) return
    poll.stop()
    status.value = response
  } catch (e) {
    if (!context.isCurrent()) return
    actionError.value = e instanceof Error ? e.message : String(e)
    if (anyBusy()) poll.start(false)
    return
  }
  poll.start(false)
}

async function cancel(source: MidiSource) {
  const context = beginAction(source)
  actionError.value = null
  try {
    const response = await midiApi.cancelTranscription(context.trackId, source)
    if (!context.isCurrent()) return
    poll.stop()
    status.value = response
  } catch (e) {
    if (!context.isCurrent()) return
    actionError.value = e instanceof Error ? e.message : String(e)
  }
  if (context.isCurrent() && anyBusy()) poll.start(false)
}

async function removeAll() {
  if (!window.confirm(t('midiPanel.confirmDeleteAll'))) return
  generation++
  actionTokens.clear()
  poll.stop()
  const session = generation
  const trackId = props.trackId
  const isCurrent = () => alive && session === generation && trackId === props.trackId
  playbackGeneration++
  activeSynth?.stop()
  activeSynth = null
  playingSource.value = null
  await midiApi.deleteMidi(trackId)
  if (!isCurrent()) return
  const response = await midiApi.getMidiStatus(trackId)
  if (!isCurrent()) return
  midiCache.value = {}
  status.value = response
  if (anyBusy()) poll.start(false)
}

function download(source: MidiSource) {
  const url = status.value?.urls[source]
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = `${source}_${props.trackId}.mid`
  a.click()
}

async function getOrLoadMidi(source: MidiSource): Promise<MidiParsed | null> {
  if (midiCache.value[source]) return midiCache.value[source]
  const url = status.value?.urls[source]
  if (!url) return null
  const session = generation
  const trackId = props.trackId
  const isCurrent = () => alive && session === generation && trackId === props.trackId && status.value?.urls[source] === url
  try {
    const resp = await fetch(url)
    if (!isCurrent()) return null
    const buf = await resp.arrayBuffer()
    if (!isCurrent()) return null
    const parsed = parseMidiBytes(buf)
    midiCache.value[source] = parsed
    return parsed
  } catch {
    return null
  }
}

async function togglePlayMidi(source: MidiSource) {
  const token = ++playbackGeneration
  if (playingSource.value === source) {
    activeSynth?.stop()
    activeSynth = null
    playingSource.value = null
    return
  }

  activeSynth?.stop()
  activeSynth = null
  playingSource.value = null

  const parsed = await getOrLoadMidi(source)
  if (!alive || token !== playbackGeneration || !parsed || parsed.notes.length === 0) return

  playingSource.value = source
  activeSynth = playMidiNotes(parsed.notes, 0, () => {
    if (token === playbackGeneration && playingSource.value === source) {
      playingSource.value = null
      activeSynth = null
    }
  })
}

async function toggleRoll(source: MidiSource) {
  if (rollTimer !== undefined) clearTimeout(rollTimer)
  const session = generation
  const trackId = props.trackId
  if (openRollSource.value === source) {
    openRollSource.value = null
    return
  }
  openRollSource.value = source
  const parsed = await getOrLoadMidi(source)
  if (alive && session === generation && openRollSource.value === source && parsed) {
    rollTimer = setTimeout(() => {
      rollTimer = undefined
      if (!alive || session !== generation || openRollSource.value !== source) return
      const canvas = document.getElementById(`roll-${trackId}-${source}`)
      if (canvas instanceof HTMLCanvasElement) drawPianoRoll(canvas, parsed.notes, parsed.durationSec)
    }, 60)
  }
}

function resetSession() {
  generation++
  playbackGeneration++
  actionTokens.clear()
  poll.stop()
  if (rollTimer !== undefined) clearTimeout(rollTimer)
  rollTimer = undefined
  activeSynth?.stop()
  activeSynth = null
  playingSource.value = null
  openRollSource.value = null
  midiCache.value = {}
  status.value = null
  actionError.value = null
}

watch(() => props.trackId, () => { resetSession(); poll.start() })
onMounted(() => poll.start())
onBeforeUnmount(() => {
  alive = false
  resetSession()
})
</script>

<template>
  <div class="rounded-lg border border-border bg-panel-2">
    <button
      type="button"
      class="flex w-full items-center justify-between px-3 py-2 text-xs font-medium text-text-dim"
      @click="expanded = !expanded"
    >
      <span>MIDI</span>
      <span aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
    </button>

    <div v-if="expanded" class="space-y-2 border-t border-border/60 p-3">
      <p class="text-[11px] text-text-dim">
        {{ t('midiPanel.intro') }}
      </p>

      <div v-if="actionError" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ actionError }}</div>

      <div
        v-for="source in status?.available || []"
        :key="source"
        class="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 bg-panel p-2"
      >
        <span class="min-w-24 text-xs text-text-dim">{{ SOURCE_LABELS[source] }}</span>

        <template v-if="isBusy(source)">
          <span class="text-xs text-text-dim">
            {{ status?.sources[source].status === 'queued' ? t('midiPanel.queued') : t('midiPanel.running') }}
          </span>
          <button type="button" class="text-xs text-text-dim hover:text-status-failed" @click="cancel(source)">{{ t('midiPanel.cancel') }}</button>
        </template>

        <template v-else-if="status?.sources[source].status === 'done'">
          <button
            type="button"
            class="rounded px-2 py-0.5 text-xs font-medium"
            :class="playingSource === source ? 'bg-status-failed text-white' : 'accent-gradient text-white'"
            @click="togglePlayMidi(source)"
          >
            {{ playingSource === source ? t('midiPanel.stop') : t('midiPanel.listen') }}
          </button>
          <button
            type="button"
            class="rounded border border-border bg-panel px-2 py-0.5 text-xs text-text hover:bg-panel-2"
            @click="toggleRoll(source)"
          >
            {{ t('midiPanel.notes') }}
          </button>
          <button type="button" class="text-xs text-accent1 hover:underline" @click="download(source)">{{ t('midiPanel.download') }}</button>
          <button type="button" class="text-xs text-text-dim hover:underline" @click="start(source, true)">{{ t('midiPanel.recreate') }}</button>

          <div v-if="openRollSource === source" class="mt-2 w-full space-y-1">
            <div class="flex items-center justify-between text-[10px] text-text-dim">
              <span>{{ t('midiPanel.pianoRoll') }}</span>
              <span v-if="midiCache[source]">{{ t('midiPanel.notesCount', { count: midiCache[source].notes.length, bpm: midiCache[source].tempoBpm }) }}</span>
            </div>
            <canvas
              :id="`roll-${trackId}-${source}`"
              class="h-28 w-full rounded border border-border/80 bg-[#161922]"
            />
          </div>
        </template>

        <template v-else>
          <button
            type="button"
            class="accent-gradient rounded-lg px-2.5 py-1 text-xs font-medium text-white"
            @click="start(source)"
          >
            {{ t('midiPanel.recognize') }}
          </button>
          <span v-if="status?.sources[source].status === 'cancelled'" class="text-xs text-text-dim">{{ t('midiPanel.cancelled') }}</span>
        </template>

        <span
          v-if="status?.sources[source].status === 'failed' && status.sources[source].error"
          class="w-full text-xs text-status-failed"
          :title="status.sources[source].error || ''"
        >
          {{ status.sources[source].error }}
        </span>
      </div>

      <button
        v-if="status && Object.keys(status.urls).length > 0"
        type="button"
        class="rounded-lg border border-status-failed/40 px-2.5 py-1 text-xs font-medium text-status-failed"
        @click="removeAll"
      >
        {{ t('midiPanel.deleteAll') }}
      </button>
    </div>
  </div>
</template>
