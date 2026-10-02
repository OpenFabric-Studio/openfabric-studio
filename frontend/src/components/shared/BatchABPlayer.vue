<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, type ComponentPublicInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import PlayIcon from './icons/PlayIcon.vue'
import PauseIcon from './icons/PauseIcon.vue'

const { t } = useI18n()

const props = defineProps<{
  sources: string[]
  labels?: string[]
  durationSec?: number | null
}>()

const activeIndex = ref(0)
const playing = ref(false)
const starting = ref(false)
const playbackFailed = ref(false)
const currentTime = ref(0)
const duration = ref(props.durationSec || 0)
const audioEls = ref<(HTMLAudioElement | null)[]>([])
let mounted = true
let generation = 0
let playRequested = false

const currentSrc = computed(() => props.sources[activeIndex.value] || '')

function formatTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function getAudio(index: number): HTMLAudioElement | null {
  return audioEls.value[index] ?? null
}

function setAudioElement(index: number, el: Element | ComponentPublicInstance | null): void {
  audioEls.value[index] = el instanceof HTMLAudioElement ? el : null
}

function eventAudio(index: number, event: Event): HTMLAudioElement | null {
  const el = event.currentTarget
  return el instanceof HTMLAudioElement && el === getAudio(index) ? el : null
}

function stopState(): void {
  generation++
  playRequested = false
  playing.value = false
  starting.value = false
}

function onLoadedMetadata(index: number, event: Event): void {
  const el = eventAudio(index, event)
  if (el && Number.isFinite(el.duration) && el.duration > 0 && (!duration.value || index === activeIndex.value)) {
    duration.value = el.duration
  }
}

function onTimeUpdate(index: number, event: Event): void {
  if (index === activeIndex.value) {
    const el = eventAudio(index, event)
    if (el) currentTime.value = el.currentTime
  }
}

function onPlay(index: number, event: Event): void {
  const el = eventAudio(index, event)
  if (!el) return
  if (!mounted || index !== activeIndex.value || !playRequested) {
    el.pause()
    releasePlaybackIfCurrent(el)
    return
  }
  playing.value = true
}

function onPause(index: number, event: Event): void {
  const el = eventAudio(index, event)
  if (!el || !el.paused) return
  if (index === activeIndex.value) stopState()
  releasePlaybackIfCurrent(el)
}

function onEnded(index: number, event: Event): void {
  const el = eventAudio(index, event)
  if (!el) return
  if (index === activeIndex.value) {
    stopState()
    currentTime.value = el.currentTime
  }
  releasePlaybackIfCurrent(el)
}

async function startAudio(index: number): Promise<void> {
  const el = getAudio(index), source = props.sources[index]
  if (!mounted || !el || !source || index !== activeIndex.value) return
  const requestGeneration = ++generation
  const isCurrent = () => mounted && generation === requestGeneration && index === activeIndex.value && getAudio(index) === el && props.sources[index] === source
  playRequested = true
  playbackFailed.value = false
  starting.value = true
  claimPlayback(el)
  try {
    await el.play()
  } catch (cause) {
    if (!isCurrent()) return
    stopState()
    releasePlaybackIfCurrent(el)
    playbackFailed.value = !(cause instanceof DOMException && cause.name === 'AbortError' && el.paused)
  } finally {
    if (isCurrent()) starting.value = false
  }
}

function togglePlay(): void {
  const el = getAudio(activeIndex.value)
  if (!el) return
  if (playing.value || playRequested) {
    stopState()
    el.pause()
    releasePlaybackIfCurrent(el)
  } else {
    void startAudio(activeIndex.value)
  }
}

function switchVariant(newIndex: number): void {
  if (newIndex === activeIndex.value || newIndex < 0 || newIndex >= props.sources.length) return
  const prevIndex = activeIndex.value
  const prevAudio = getAudio(prevIndex)
  const nextAudio = getAudio(newIndex)
  const previousTime = prevAudio ? prevAudio.currentTime : currentTime.value
  const pos = Number.isFinite(previousTime) && previousTime > 0 ? previousTime : 0
  const resume = playRequested
  stopState()
  activeIndex.value = newIndex
  currentTime.value = pos
  playbackFailed.value = false
  if (prevAudio) {
    prevAudio.pause()
    releasePlaybackIfCurrent(prevAudio)
  }

  if (nextAudio) {
    try {
      nextAudio.currentTime = pos
    } catch {}
    if (Number.isFinite(nextAudio.duration) && nextAudio.duration > 0) duration.value = nextAudio.duration
    if (resume) void startAudio(newIndex)
  }
}

function onSeek(e: Event): void {
  if (!(e.target instanceof HTMLInputElement)) return
  const requestedTime = Number(e.target.value)
  if (!Number.isFinite(requestedTime)) return
  const val = Math.min(duration.value || 1, Math.max(0, requestedTime))
  currentTime.value = val
  const el = getAudio(activeIndex.value)
  if (el) el.currentTime = val
}

function downloadCurrent() {
  const url = currentSrc.value
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = `variant_${activeIndex.value + 1}_${Date.now()}`
  a.click()
}

watch(
  () => props.sources.slice(),
  (sources, previous) => {
    if (sources.length === previous.length && sources.every((source, index) => source === previous[index])) return
    stopState()
    playbackFailed.value = false
    for (const el of audioEls.value) {
      if (el) { el.pause(); releasePlaybackIfCurrent(el) }
    }
    if (activeIndex.value >= props.sources.length) {
      activeIndex.value = 0
    }
    currentTime.value = 0
    duration.value = props.durationSec || 0
  },
)

onBeforeUnmount(() => {
  mounted = false
  stopState()
  for (const el of audioEls.value) {
    if (el) {
      el.pause()
      releasePlaybackIfCurrent(el)
      el.removeAttribute('src')
      el.load()
    }
  }
})
</script>

<template>
  <div class="space-y-2 rounded-xl border border-border bg-panel-2 p-3">
    <!-- Hidden audio elements for zero-latency seamless A/B switching -->
    <audio
      v-for="(src, idx) in sources"
      :key="src"
      :ref="(el) => setAudioElement(idx, el)"
      :src="src"
      preload="auto"
      @loadedmetadata="onLoadedMetadata(idx, $event)"
      @timeupdate="onTimeUpdate(idx, $event)"
      @play="onPlay(idx, $event)"
      @pause="onPause(idx, $event)"
      @ended="onEnded(idx, $event)"
    />

    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 pb-2">
      <div class="flex items-center gap-1.5">
        <span class="text-[11px] font-medium text-text-dim">{{ t('batchAB.compareLabel') }}</span>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="(src, idx) in sources"
            :key="src"
            type="button"
            class="rounded-lg px-2.5 py-1 text-xs font-medium transition-all"
            :class="
              idx === activeIndex
                ? 'accent-gradient text-white shadow-sm ring-1 ring-accent1/50'
                : 'border border-border bg-panel text-text-dim hover:text-text'
            "
            @click="switchVariant(idx)"
          >
            {{ labels?.[idx] || t('batchAB.variant', { n: idx + 1 }) }}
          </button>
        </div>
      </div>

      <button
        type="button"
        class="text-xs text-accent1 hover:underline"
        :title="t('batchAB.downloadTitle')"
        @click="downloadCurrent"
      >
        {{ t('batchAB.downloadVariant', { n: activeIndex + 1 }) }}
      </button>
    </div>

    <!-- Playback bar -->
    <div class="flex items-center gap-3">
      <button
        type="button"
        class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full accent-gradient text-white transition hover:opacity-90"
        :aria-label="playing || starting ? t('waveformPlayer.pause') : t('waveformPlayer.play')"
        :aria-busy="starting"
        :disabled="!currentSrc"
        @click="togglePlay"
      >
        <PlayIcon v-if="!playing && !starting" aria-hidden="true" class="w-[13px] h-[13px]" />
        <PauseIcon v-else aria-hidden="true" class="w-[13px] h-[13px]" />
      </button>

      <span class="w-20 shrink-0 text-xs tabular-nums text-text-dim">
        {{ formatTime(currentTime) }} / {{ formatTime(duration) }}
      </span>

      <input
        type="range"
        min="0"
        :max="duration || 1"
        step="0.05"
        :value="currentTime"
        :aria-label="t('waveformPlayer.seek')"
        class="h-2 flex-1 cursor-pointer accent-current"
        @input="onSeek"
      />
    </div>
    <p v-if="playbackFailed" role="alert" class="text-xs text-status-failed">{{ t('waveformPlayer.playbackFailed') }} <a :href="currentSrc" target="_blank" rel="noopener" class="underline">{{ t('waveformPlayer.openAudio') }}</a></p>
  </div>
</template>
