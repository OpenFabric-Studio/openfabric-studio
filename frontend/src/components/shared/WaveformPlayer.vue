<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { claimPlayback, fetchAndComputePeaks, peaksCache, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import PlayIcon from './icons/PlayIcon.vue'
import PauseIcon from './icons/PauseIcon.vue'

const { t } = useI18n()

const props = defineProps<{ src: string; compact?: boolean }>()
const emit = defineEmits<{ playing: [value: boolean] }>()

const BAR_COUNT = 140

const audioEl = ref<HTMLAudioElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const playing = ref(false)
watch(playing, value => emit('playing', value), { flush: 'sync' })
const duration = ref(0)
const currentTime = ref(0)
const peaks = ref<number[]>([])
const playbackFailed = ref(false)
const waveformFailed = ref(false)
const starting = ref(false)
let generation = 0
let mounted = true

const timeLabel = computed(() => formatTime(currentTime.value > 0 ? currentTime.value : duration.value))

function formatTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

async function loadPeaks(priority = false): Promise<void> {
  if (peaks.value.length > 0) return
  const cached = peaksCache.get(props.src)
  if (cached) {
    peaks.value = cached
    draw()
    return
  }
  const requestGeneration = generation
  const source = props.src
  try {
    const result = await fetchAndComputePeaks(source, BAR_COUNT, priority)
    if (!mounted || requestGeneration !== generation) return
    peaks.value = result
    waveformFailed.value = false
  } catch {
    if (!mounted || requestGeneration !== generation) return
    waveformFailed.value = true
  }
  draw()
}

function draw() {
  const canvas = canvasEl.value
  if (!canvas) return
  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  const width = Math.max(1, rect.width)
  const height = Math.max(1, rect.height || 40)
  canvas.width = width * dpr
  canvas.height = height * dpr
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)

  const bars = peaks.value.length ? peaks.value : new Array(BAR_COUNT).fill(0.1)
  const gap = 2
  const barWidth = Math.max(1, width / bars.length - gap)
  const progress = duration.value ? currentTime.value / duration.value : 0
  const progressX = width * progress

  const gradient = ctx.createLinearGradient(0, 0, width, 0)
  gradient.addColorStop(0, '#a855f7')
  gradient.addColorStop(1, '#ec4899')

  bars.forEach((v, i) => {
    const x = i * (barWidth + gap)
    const barHeight = Math.max(2, v * height)
    const y = (height - barHeight) / 2
    ctx.fillStyle = x <= progressX ? gradient : 'rgba(255,255,255,0.18)'
    ctx.fillRect(x, y, barWidth, barHeight)
  })
}

async function play(): Promise<void> {
  const audio = audioEl.value
  if (!mounted || !audio || !props.src || starting.value || playing.value) return
  const requestGeneration = generation
  playbackFailed.value = false
  starting.value = true
  claimPlayback(audio)
  if (!peaks.value.length) {
    void loadPeaks(true)
  }
  try {
    await audio.play()
  } catch (cause) {
    if (mounted && requestGeneration === generation) {
      if (cause instanceof DOMException && cause.name === 'AbortError' && audio.paused) {
        playing.value = false
        releasePlaybackIfCurrent(audio)
      } else {
        onMediaError()
      }
    }
  } finally {
    if (mounted && requestGeneration === generation) starting.value = false
  }
}

function togglePlay(): void {
  const audio = audioEl.value
  if (!audio || starting.value) return
  if (playing.value) {
    audio.pause()
    releasePlaybackIfCurrent(audio)
  } else {
    void play()
  }
}

defineExpose({ play })

function onMediaError(): void {
  playbackFailed.value = true
  playing.value = false
  starting.value = false
  if (audioEl.value) releasePlaybackIfCurrent(audioEl.value)
}

function onLoaded() {
  const value = audioEl.value?.duration ?? 0
  duration.value = Number.isFinite(value) && value > 0 ? value : 0
  draw()
}
function onTimeUpdate() {
  if (audioEl.value) currentTime.value = audioEl.value.currentTime
  draw()
}
function onEnded() {
  playing.value = false
  if (audioEl.value) releasePlaybackIfCurrent(audioEl.value)
  draw()
}

function onSeekClick(evt: MouseEvent) {
  const audio = audioEl.value
  const canvas = canvasEl.value
  if (!audio || !canvas || !duration.value) return
  const rect = canvas.getBoundingClientRect()
  if (rect.width <= 0) return
  const ratio = Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width))
  audio.currentTime = ratio * duration.value
}

function onSeekKey(event: KeyboardEvent): void {
  const audio = audioEl.value
  if (!audio || !duration.value) return
  const position = event.key === 'Home' ? 0 : event.key === 'End' ? duration.value
    : event.key === 'ArrowRight' || event.key === 'ArrowUp' ? audio.currentTime + 5
      : event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? audio.currentTime - 5 : null
  if (position === null) return
  event.preventDefault()
  audio.currentTime = Math.min(duration.value, Math.max(0, position))
  currentTime.value = audio.currentTime
  draw()
}

let resizeObserver: ResizeObserver | null = null
let intersectionObserver: IntersectionObserver | null = null

function observeVisibility() {
  intersectionObserver?.disconnect()
  intersectionObserver = null
  if (!canvasEl.value) return

  const cached = peaksCache.get(props.src)
  if (cached) {
    peaks.value = cached
    draw()
    return
  }

  if (typeof IntersectionObserver !== 'undefined') {
    intersectionObserver = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          intersectionObserver?.disconnect()
          intersectionObserver = null
          void loadPeaks()
        }
      },
      { rootMargin: '200px' },
    )
    intersectionObserver.observe(canvasEl.value)
  } else {
    void loadPeaks()
  }
}

watch(
  () => props.src,
  () => {
    generation++
    if (audioEl.value) {
      audioEl.value.pause()
      releasePlaybackIfCurrent(audioEl.value)
      audioEl.value.load()
    }
    playing.value = false
    starting.value = false
    playbackFailed.value = false
    waveformFailed.value = false
    peaks.value = []
    duration.value = 0
    currentTime.value = 0
    draw()
    observeVisibility()
  },
  { flush: 'post' },
)

onMounted(() => {
  draw()
  if (canvasEl.value) {
    resizeObserver = new ResizeObserver(() => draw())
    resizeObserver.observe(canvasEl.value)
    observeVisibility()
  }
})

onBeforeUnmount(() => {
  playing.value = false
  mounted = false
  generation++
  intersectionObserver?.disconnect()
  resizeObserver?.disconnect()
  if (audioEl.value) {
    audioEl.value.pause()
    releasePlaybackIfCurrent(audioEl.value)
  }
})
</script>

<template>
  <div class="space-y-2">
  <div class="flex items-center" :class="compact ? 'gap-2' : 'gap-3'">
    <button
      type="button"
      class="flex shrink-0 items-center justify-center rounded-full accent-gradient text-white"
      :class="compact ? 'h-9 w-9' : 'h-10 w-10'"
      :aria-label="playing ? t('waveformPlayer.pause') : t('waveformPlayer.play')"
      :disabled="starting || !src"
      :aria-busy="starting"
      @click="togglePlay"
    >
      <PlayIcon v-if="!playing" aria-hidden="true" :width="compact ? 11 : 14" :height="compact ? 11 : 14" />
      <PauseIcon v-else aria-hidden="true" :width="compact ? 11 : 14" :height="compact ? 11 : 14" />
    </button>
    <canvas ref="canvasEl" class="min-w-0 flex-1 cursor-pointer rounded bg-panel-2 focus-visible:outline-2 focus-visible:outline-accent" :class="compact ? 'h-7' : 'h-10'"
      role="slider" :tabindex="duration > 0 ? 0 : -1" :aria-label="t('waveformPlayer.seek')"
      :aria-valuemin="0" :aria-valuemax="duration" :aria-valuenow="currentTime" :aria-valuetext="formatTime(currentTime)"
      @click="onSeekClick" @keydown="onSeekKey"></canvas>
    <span class="w-10 shrink-0 text-right text-xs tabular-nums text-text-dim">{{ timeLabel }}</span>
    <audio ref="audioEl" :src="src" preload="none" class="hidden" @timeupdate="onTimeUpdate" @ended="onEnded" @loadedmetadata="onLoaded" @play="playing = true" @pause="playing = false" @error="onMediaError"></audio>
  </div>
  <p v-if="playbackFailed" role="alert" class="text-xs text-status-failed">{{ t('waveformPlayer.playbackFailed') }} <a :href="src" target="_blank" rel="noopener" class="underline">{{ t('waveformPlayer.openAudio') }}</a></p>
  <p v-else-if="waveformFailed" class="text-xs text-text-dim">{{ t('waveformPlayer.waveformUnavailable') }}</p>
  </div>
</template>
