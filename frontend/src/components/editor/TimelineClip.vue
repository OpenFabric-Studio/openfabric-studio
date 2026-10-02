<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { stretchFactor } from '../../audio/timelineTypes'
import type { Clip } from '../../audio/timelineTypes'
import { hasOpenDialog } from '../../composables/useDialogA11y'

const MIN_CLIP_SEC = 0.05
const SNAP_PX = 8

const props = defineProps<{
  clip: Clip
  pxPerSecond: number
  buffer: AudioBuffer | null
  /** Original source duration; buffer may still be waiting for its warped replacement. */
  sourceDuration?: number
  snapCandidates: number[]
  gridStepSec: number
  snapEnabled: boolean
  selected: boolean
  laneName: string
  theme: {
    baseHex: string
    rmsLight: string
    rmsMain: string
    rmsDark: string
    peakLight: string
    peakMain: string
  }
}>()

const emit = defineEmits<{
  move: [timelineStart: number]
  trim: [payload: { trimStart: number; trimEnd: number; timelineStart: number }]
  fade: [payload: { fadeInDuration: number; fadeOutDuration: number }]
  dragEnd: []
  select: []
  remove: []
  toggleWarp: [enabled: boolean]
  toggleMute: [enabled: boolean]
  toggleSolo: [enabled: boolean]
}>()

import { useI18n } from 'vue-i18n'
import { useEditorStore } from '../../stores/editor'
const { t } = useI18n()
const store = useEditorStore()

const effectiveStretchFactor = computed(() => {
  return stretchFactor(props.clip, store.project.bpm)
})

const canvasEl = ref<HTMLCanvasElement | null>(null)

const duration = computed(() => (props.clip.trimEnd - props.clip.trimStart) * effectiveStretchFactor.value)
const left = computed(() => props.clip.timelineStart * props.pxPerSecond)
const width = computed(() => Math.max(4, duration.value * props.pxPerSecond))
const minimumSourceLength = computed(() => MIN_CLIP_SEC / effectiveStretchFactor.value)
const sourceEnd = computed(() => props.sourceDuration ?? (props.buffer
  ? props.buffer.duration / effectiveStretchFactor.value
  : (props.clip.notes ?? []).reduce((end, note) => Math.max(end, note.startSec + note.durationSec), props.clip.trimEnd)))
const canTrimEnd = computed(() => sourceEnd.value > props.clip.trimStart)
const startTrimBounds = computed(() => leftTrimBounds(props.clip.trimStart, props.clip.trimEnd, props.clip.timelineStart))
const endTrimMinimum = computed(() => canTrimEnd.value ? Math.min(sourceEnd.value, props.clip.trimStart + minimumSourceLength.value) : props.clip.trimEnd)
const startTrimMinTimeline = computed(() => Math.max(0, props.clip.timelineStart + (startTrimBounds.value.min - props.clip.trimStart) * effectiveStretchFactor.value))
const startTrimMaxTimeline = computed(() => props.clip.timelineStart + (startTrimBounds.value.max - props.clip.trimStart) * effectiveStretchFactor.value)
const endTrimMinTimeline = computed(() => props.clip.timelineStart + (endTrimMinimum.value - props.clip.trimStart) * effectiveStretchFactor.value)
const endTrimMaxTimeline = computed(() => props.clip.timelineStart + ((canTrimEnd.value ? sourceEnd.value : props.clip.trimEnd) - props.clip.trimStart) * effectiveStretchFactor.value)
const endTrimValue = computed(() => Math.max(endTrimMinTimeline.value, Math.min(props.clip.timelineStart + duration.value, endTrimMaxTimeline.value)))
const fadeInValue = computed(() => Math.max(0, Math.min(props.clip.fadeInDuration ?? 0, duration.value)))
const fadeOutValue = computed(() => Math.max(0, Math.min(props.clip.fadeOutDuration ?? 0, duration.value)))

function leftTrimBounds(trimStart: number, trimEnd: number, timelineStart: number): { min: number; max: number } {
  const min = Math.max(0, trimStart - timelineStart / effectiveStretchFactor.value)
  return { min, max: Math.max(min, trimEnd - minimumSourceLength.value) }
}

function boundedLeftTrim(value: number, trimStart = props.clip.trimStart, trimEnd = props.clip.trimEnd, timelineStart = props.clip.timelineStart) {
  const bounds = leftTrimBounds(trimStart, trimEnd, timelineStart)
  const nextStart = Math.max(bounds.min, Math.min(value, bounds.max))
  return { trimStart: nextStart, trimEnd, timelineStart: Math.max(0, timelineStart + (nextStart - trimStart) * effectiveStretchFactor.value) }
}

function boundedRightTrim(value: number, trimStart = props.clip.trimStart, timelineStart = props.clip.timelineStart) {
  if (sourceEnd.value <= trimStart) return { trimStart, trimEnd: props.clip.trimEnd, timelineStart }
  const minimum = Math.min(sourceEnd.value, trimStart + minimumSourceLength.value)
  return { trimStart, trimEnd: Math.max(minimum, Math.min(value, sourceEnd.value)), timelineStart }
}

type DragMode = 'move' | 'trim-left' | 'trim-right' | 'fade-left' | 'fade-right'
let dragMode: DragMode | null = null
let dragStartClientX = 0
let dragStartTimelineStart = 0
let dragStartTrimStart = 0
let dragStartTrimEnd = 0
let dragStartFadeIn = 0
let dragStartFadeOut = 0
let dragCandidates: number[] = []

const fadeInPolygon = computed(() => {
  const dur = duration.value
  if (dur <= 0) return ''
  const pct = Math.min(100, ((props.clip.fadeInDuration || 0) / dur) * 100)
  return `0,0 ${pct},0 0,100`
})

const fadeOutPolygon = computed(() => {
  const dur = duration.value
  if (dur <= 0) return ''
  const pct = Math.min(100, ((props.clip.fadeOutDuration || 0) / dur) * 100)
  return `100,0 ${100 - pct},0 100,100`
})

function snap(value: number): number {
  const thresholdSec = SNAP_PX / props.pxPerSecond
  let best = value
  let bestDist = thresholdSec
  
  for (const c of dragCandidates) {
    const d = Math.abs(c - value)
    if (d < bestDist) {
      bestDist = d
      best = c
    }
  }
  
  if (props.snapEnabled && props.gridStepSec > 0) {
    const gridSnapped = Math.round(value / props.gridStepSec) * props.gridStepSec
    const d = Math.abs(gridSnapped - value)
    if (d < bestDist) {
      bestDist = d
      best = gridSnapped
    }
  }
  
  return best
}

/** Arrow keys on the focused clip: move it (grid step with Magnet on, else
 * 0.1 s; Alt for 0.01 s), or with Shift move its end. Each press is one undo step. */
function onKeydown(e: KeyboardEvent): void {
  if (hasOpenDialog()) return
  if (e.target !== e.currentTarget) return
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
  if (e.ctrlKey || e.metaKey) return
  e.preventDefault()
  e.stopPropagation()
  if (e.shiftKey && !canTrimEnd.value) return
  const dir = e.key === 'ArrowLeft' ? -1 : 1
  const grid = props.snapEnabled && props.gridStepSec > 0 && !e.altKey
  const step = e.altKey ? 0.01 : grid ? props.gridStepSec : 0.1
  if (e.shiftKey) {
    const sf = effectiveStretchFactor.value
    emit('trim', boundedRightTrim(props.clip.trimEnd + (dir * step) / sf))
  } else {
    let start = props.clip.timelineStart + dir * step
    if (grid) start = Math.round(start / props.gridStepSec) * props.gridStepSec
    emit('move', Math.max(0, start))
  }
  emit('dragEnd')
}

/** Values and limits are handle positions in timeline seconds, including the
 * fade-out's inner edge, so arrows and Home/End agree with the slider values. */
function onHandleKeydown(handle: Exclude<DragMode, 'move'>, event: KeyboardEvent): void {
  if (hasOpenDialog() || event.ctrlKey || event.metaKey) return
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  event.stopPropagation()
  if (handle === 'trim-right' && !canTrimEnd.value) return
  const base = event.altKey ? 0.01 : props.snapEnabled && props.gridStepSec > 0 ? props.gridStepSec : 0.1
  const delta = base * (event.shiftKey ? 4 : 1) * (event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 1)
  const home = event.key === 'Home', end = event.key === 'End'
  if (handle === 'trim-left') {
    const value = home ? startTrimBounds.value.min : end ? startTrimBounds.value.max : props.clip.trimStart + delta / effectiveStretchFactor.value
    emit('trim', boundedLeftTrim(value))
  } else if (handle === 'trim-right') {
    const value = home ? endTrimMinimum.value : end ? sourceEnd.value : props.clip.trimEnd + delta / effectiveStretchFactor.value
    emit('trim', boundedRightTrim(value))
  } else if (handle === 'fade-left') {
    emit('fade', { fadeInDuration: home ? 0 : end ? duration.value : Math.max(0, Math.min(fadeInValue.value + delta, duration.value)), fadeOutDuration: fadeOutValue.value })
  } else {
    emit('fade', { fadeInDuration: fadeInValue.value, fadeOutDuration: home ? duration.value : end ? 0 : Math.max(0, Math.min(fadeOutValue.value - delta, duration.value)) })
  }
  emit('dragEnd')
}

function onPointerDown(mode: DragMode, evt: PointerEvent): void {
  evt.stopPropagation()
  if (mode === 'trim-right' && !canTrimEnd.value) return
  emit('select')
  dragMode = mode
  dragStartClientX = evt.clientX
  dragStartTimelineStart = props.clip.timelineStart
  dragStartTrimStart = props.clip.trimStart
  dragStartTrimEnd = props.clip.trimEnd
  dragStartFadeIn = props.clip.fadeInDuration || 0
  dragStartFadeOut = props.clip.fadeOutDuration || 0
  const ownStart = props.clip.timelineStart
  const ownEnd = props.clip.timelineStart + duration.value
  dragCandidates = props.snapCandidates.filter((c) => Math.abs(c - ownStart) > 1e-6 && Math.abs(c - ownEnd) > 1e-6)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
}

function onPointerMove(evt: PointerEvent): void {
  if (!dragMode) return
  const deltaSec = (evt.clientX - dragStartClientX) / props.pxPerSecond

  if (dragMode === 'move') {
    const raw = Math.max(0, dragStartTimelineStart + deltaSec)
    emit('move', snap(raw))
  } else if (dragMode === 'trim-left') {
    const raw = boundedLeftTrim(dragStartTrimStart + deltaSec / effectiveStretchFactor.value, dragStartTrimStart, dragStartTrimEnd, dragStartTimelineStart)
    const snappedStart = dragStartTrimStart + (snap(raw.timelineStart) - dragStartTimelineStart) / effectiveStretchFactor.value
    emit('trim', boundedLeftTrim(snappedStart, dragStartTrimStart, dragStartTrimEnd, dragStartTimelineStart))
  } else if (dragMode === 'trim-right') {
    const raw = boundedRightTrim(dragStartTrimEnd + deltaSec / effectiveStretchFactor.value, dragStartTrimStart, dragStartTimelineStart)
    const rawEnd = dragStartTimelineStart + (raw.trimEnd - dragStartTrimStart) * effectiveStretchFactor.value
    const snappedEnd = dragStartTrimStart + (snap(rawEnd) - dragStartTimelineStart) / effectiveStretchFactor.value
    emit('trim', boundedRightTrim(snappedEnd, dragStartTrimStart, dragStartTimelineStart))
  } else if (dragMode === 'fade-left') {
    const rawFadeIn = Math.max(0, dragStartFadeIn + deltaSec)
    emit('fade', {
      fadeInDuration: Math.min(rawFadeIn, duration.value),
      fadeOutDuration: props.clip.fadeOutDuration || 0,
    })
  } else if (dragMode === 'fade-right') {
    const rawFadeOut = Math.max(0, dragStartFadeOut - deltaSec)
    emit('fade', {
      fadeInDuration: props.clip.fadeInDuration || 0,
      fadeOutDuration: Math.min(rawFadeOut, duration.value),
    })
  }
}

function onPointerUp(): void {
  dragMode = null
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  drawPeaks()
  emit('dragEnd')
}

// Deliberately only recomputed on buffer load and drag-end (pointerup), not
// on every pointermove - recomputing RMS peaks continuously while dragging a
// long clip would visibly stutter. During a drag the existing bitmap just
// stretches with the container (a stale-but-cheap visual, sharp again once
// the drag ends and this redraws at the final size).
function drawPeaks(): void {
  const canvas = canvasEl.value
  if (!canvas) return
  
  const isMidi = props.clip.type === 'midi'
  const buffer = props.buffer
  if (!isMidi && !buffer) return
  
  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  const w = Math.max(1, rect.width)
  const h = Math.max(1, rect.height || 28)
  canvas.width = w * dpr
  canvas.height = h * dpr
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  const sf = effectiveStretchFactor.value

  if (isMidi && props.clip.notes) {
    const notes = props.clip.notes
    if (notes.length === 0) return
    
    // Find min and max note to scale vertically
    let minNote = 127
    let maxNote = 0
    for (const n of notes) {
      if (n.note < minNote) minNote = n.note
      if (n.note > maxNote) maxNote = n.note
    }
    const noteRange = Math.max(12, maxNote - minNote + 2) // at least an octave
    
    const clipDuration = (props.clip.trimEnd - props.clip.trimStart) * sf
    if (clipDuration <= 0) return
    
    ctx.fillStyle = '#6366f1' // accent color
    
    for (const n of notes) {
      const startSec = n.startSec * sf
      const durSec = n.durationSec * sf
      // Only draw if it's within trim range
      if (startSec + durSec <= props.clip.trimStart * sf || startSec >= props.clip.trimEnd * sf) continue
      
      const pxPerSec = w / clipDuration
      const startPx = (startSec - props.clip.trimStart * sf) * pxPerSec
      const widthPx = Math.max(2, durSec * pxPerSec - 1)
      
      // Vertical placement
      const normalizedPitch = 1.0 - (n.note - minNote + 1) / noteRange
      const y = normalizedPitch * h
      const noteHeight = Math.max(2, h / noteRange - 1)
      
      // opacity based on velocity
      ctx.globalAlpha = Math.max(0.3, n.velocity)
      ctx.fillRect(startPx, y, widthPx, noteHeight)
    }
    ctx.globalAlpha = 1.0
    return
  }

  // Audio drawing logic
  if (!buffer) return
  const sampleRate = buffer.sampleRate
  const startSample = Math.max(0, Math.floor((props.clip.trimStart * sf) * sampleRate))
  const endSample = Math.min(buffer.length, Math.floor((props.clip.trimEnd * sf) * sampleRate))
  const totalSamples = Math.max(1, endSample - startSample)
  
  // 1 point per pixel for a perfectly smooth curve
  const pointCount = Math.max(2, Math.floor(w))
  const stepSize = totalSamples / pointCount
  
  // Minimum window size to ensure we capture full wave cycles (e.g. 10ms = ~441 samples)
  // This prevents the envelope from looking jagged when zoomed in.
  const windowSize = Math.max(400, Math.floor(stepSize * 2))
  const channel = buffer.getChannelData(0)

  // Glassy Reflection Gradients based on theme
  const rmsGradient = ctx.createLinearGradient(0, 0, 0, h)
  rmsGradient.addColorStop(0, props.theme.rmsLight || 'rgba(255, 255, 255, 0.95)')
  rmsGradient.addColorStop(0.4, props.theme.rmsMain || 'rgba(192, 132, 252, 0.9)')
  rmsGradient.addColorStop(0.5, props.theme.rmsMain || 'rgba(168, 85, 247, 0.95)')
  rmsGradient.addColorStop(0.51, props.theme.rmsDark || 'rgba(126, 34, 206, 0.5)') // sharp reflection line
  rmsGradient.addColorStop(1, 'rgba(255, 255, 255, 0.05)')

  const peakGradient = ctx.createLinearGradient(0, 0, 0, h)
  peakGradient.addColorStop(0, props.theme.peakLight || 'rgba(216, 180, 254, 0.35)')
  peakGradient.addColorStop(0.5, props.theme.peakMain || 'rgba(168, 85, 247, 0.4)')
  peakGradient.addColorStop(0.51, props.theme.rmsDark || 'rgba(126, 34, 206, 0.2)')
  peakGradient.addColorStop(1, 'rgba(255, 255, 255, 0.0)')

  const points = []

  for (let i = 0; i < pointCount; i++) {
    const centerSample = startSample + Math.floor(i * stepSize)
    const start = Math.max(startSample, Math.floor(centerSample - windowSize / 2))
    const end = Math.min(endSample, Math.floor(centerSample + windowSize / 2))
    
    let sum = 0
    let peak = 0
    let count = 0
    for (let j = start; j < end; j++) {
      const v = channel[j]
      const absV = Math.abs(v)
      if (absV > peak) peak = absV
      sum += v * v
      count++
    }
    const rms = count > 0 ? Math.sqrt(sum / count) : 0
    
    const peakAmp = Math.min(1, peak * 1.1)
    const rmsAmp = Math.min(1, rms * 1.4)
    
    const peakH = Math.max(0, peakAmp * h)
    const rmsH = Math.max(0, Math.min(peakH, rmsAmp * h))
    
    points.push({
      x: (i / (pointCount - 1)) * w,
      peakH,
      rmsH
    })
  }

  // Draw Peak Polygon
  ctx.fillStyle = peakGradient
  ctx.beginPath()
  ctx.moveTo(points[0].x, h/2 - points[0].peakH / 2)
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(points[i].x, h/2 - points[i].peakH / 2)
  }
  for (let i = points.length - 1; i >= 0; i--) {
    ctx.lineTo(points[i].x, h/2 + points[i].peakH / 2)
  }
  ctx.closePath()
  ctx.fill()

  // Draw RMS Polygon
  ctx.fillStyle = rmsGradient
  ctx.beginPath()
  ctx.moveTo(points[0].x, h/2 - points[0].rmsH / 2)
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(points[i].x, h/2 - points[i].rmsH / 2)
  }
  for (let i = points.length - 1; i >= 0; i--) {
    ctx.lineTo(points[i].x, h/2 + points[i].rmsH / 2)
  }
  ctx.closePath()
  ctx.fill()
}

watch(() => props.buffer, drawPeaks)
watch(() => props.theme, drawPeaks, { deep: true })
onMounted(drawPeaks)
onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
})
</script>

<template>
  <div
    class="absolute top-2 h-16 overflow-hidden rounded-lg border select-none transition-all duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
    tabindex="0"
    :aria-label="t('timeline.clipLabel', { name: laneName, start: clip.timelineStart.toFixed(2), length: duration.toFixed(2) })"
    @focus="emit('select')"
    @keydown="onKeydown"
    :class="selected ? 'bg-gradient-to-b from-panel to-panel-2 shadow-md' : 'border-border/60 bg-gradient-to-b from-panel-2 to-[#14141a] shadow-sm hover:border-border/90'"
    :style="{ 
      left: left + 'px', 
      width: width + 'px',
      borderColor: selected ? theme.baseHex : '',
      boxShadow: selected ? `0 0 10px ${theme.baseHex}33` : ''
    }"
  >
    <div class="flex h-full cursor-grab flex-col" @pointerdown="onPointerDown('move', $event)">
      <div class="flex items-center justify-between px-2 pt-1 pb-0.5 text-[10px] font-medium text-text-dim/80 bg-gradient-to-b from-white/[0.08] to-transparent">
        <span class="truncate text-text drop-shadow-md">{{ laneName }}</span>
        <div class="flex items-center gap-1 z-10">
          <button 
            type="button" 
            class="shrink-0 px-1 hover:text-white transition-transform hover:scale-110 active:scale-95"
            :class="clip.muted ? 'text-accent bg-accent/20 rounded font-bold' : ''"
            :aria-pressed="!!clip.muted"
            :aria-label="t('timeline.muteClip')"
            :title="t('timeline.muteClip')"
            @pointerdown.stop
            @click="emit('toggleMute', !clip.muted)"
          >M</button>
          <button
            type="button"
            class="shrink-0 px-1 hover:text-white transition-transform hover:scale-110 active:scale-95"
            :class="clip.solo ? 'text-accent1 bg-accent1/20 rounded font-bold' : ''"
            :aria-pressed="!!clip.solo"
            :aria-label="t('timeline.soloClip')"
            :title="t('timeline.soloClip')"
            @pointerdown.stop
            @click="emit('toggleSolo', !clip.solo)"
          >S</button>
          <button
            type="button"
            class="shrink-0 px-1 hover:text-white transition-transform hover:scale-110 active:scale-95"
            :class="clip.warpEnabled ? 'text-accent1 font-bold drop-shadow-[0_0_4px_var(--color-accent1)]' : ''"
            :aria-pressed="!!clip.warpEnabled"
            :aria-label="t('timeline.warpClip')"
            :title="t('timeline.warpClip')"
            @pointerdown.stop
            @click="emit('toggleWarp', !clip.warpEnabled)"
          >W</button>
          <button
            type="button"
            class="shrink-0 text-text-dim hover:text-status-failed transition-colors"
            :aria-label="t('timeline.removeClip')"
            :title="t('timeline.removeClip')"
            @pointerdown.stop
            @click="emit('remove')"
          >✕</button>
        </div>
      </div>
      <div
        v-if="!buffer && clip.type !== 'midi'"
        class="px-2 text-[10px] text-status-failed truncate"
        :title="clip.sourceLabel"
      >
        {{ t('editor.sourceMissing') }}
      </div>
      <canvas ref="canvasEl" class="w-full min-h-0 flex-1"></canvas>
    </div>
    
    <svg class="pointer-events-none absolute inset-0 h-full w-full opacity-40 mix-blend-overlay" preserveAspectRatio="none" viewBox="0 0 100 100">
      <polygon :points="fadeInPolygon" fill="black" />
      <polygon :points="fadeOutPolygon" fill="black" />
    </svg>

    <div class="absolute top-0 left-0 h-full w-2 cursor-ew-resize hover:bg-accent1/30 focus-visible:bg-accent1/30 focus-visible:outline-2 focus-visible:outline-accent1 transition-colors"
      role="slider" tabindex="0" aria-orientation="horizontal"
      :aria-label="t('upstreamWorkspace.trimStart')" :title="t('upstreamWorkspace.handleHint')"
      :aria-valuemin="startTrimMinTimeline" :aria-valuemax="startTrimMaxTimeline" :aria-valuenow="clip.timelineStart"
      @focus="emit('select')" @keydown="onHandleKeydown('trim-left', $event)" @pointerdown="onPointerDown('trim-left', $event)">
      <div class="absolute left-0 top-1/2 h-4 w-1 -translate-y-1/2 rounded-r-sm bg-white/40 shadow-sm"></div>
    </div>
    <div class="absolute top-0 right-0 h-full w-2 cursor-ew-resize hover:bg-accent1/30 focus-visible:bg-accent1/30 focus-visible:outline-2 focus-visible:outline-accent1 transition-colors"
      role="slider" tabindex="0" aria-orientation="horizontal"
      :aria-label="t('upstreamWorkspace.trimEnd')" :title="t('upstreamWorkspace.handleHint')"
      :aria-disabled="!canTrimEnd"
      :aria-valuemin="endTrimMinTimeline" :aria-valuemax="endTrimMaxTimeline" :aria-valuenow="endTrimValue"
      @focus="emit('select')" @keydown="onHandleKeydown('trim-right', $event)" @pointerdown="onPointerDown('trim-right', $event)">
      <div class="absolute right-0 top-1/2 h-4 w-1 -translate-y-1/2 rounded-l-sm bg-white/40 shadow-sm"></div>
    </div>
    
    <div
      class="group absolute top-0 z-20 flex h-6 w-6 -translate-x-1/2 cursor-ew-resize items-start justify-center rounded focus-visible:outline-2 focus-visible:outline-accent1"
      :style="{ left: fadeInValue * props.pxPerSecond + 'px' }"
      role="slider" tabindex="0" aria-orientation="horizontal"
      :aria-label="t('upstreamWorkspace.fadeIn')" :title="t('upstreamWorkspace.handleHint')"
      :aria-valuemin="0" :aria-valuemax="duration" :aria-valuenow="fadeInValue"
      @focus="emit('select')" @keydown="onHandleKeydown('fade-left', $event)"
      @pointerdown="onPointerDown('fade-left', $event)"
    >
      <div class="mt-1 h-2.5 w-2.5 rounded-full bg-accent1 shadow-[0_0_8px_var(--color-accent1)] transition-all group-hover:scale-125 group-hover:bg-white"></div>
    </div>
    <div
      class="group absolute top-0 z-20 flex h-6 w-6 translate-x-1/2 cursor-ew-resize items-start justify-center rounded focus-visible:outline-2 focus-visible:outline-accent1"
      :style="{ right: fadeOutValue * props.pxPerSecond + 'px' }"
      role="slider" tabindex="0" aria-orientation="horizontal"
      :aria-label="t('upstreamWorkspace.fadeOut')" :title="t('upstreamWorkspace.handleHint')"
      :aria-valuemin="0" :aria-valuemax="duration" :aria-valuenow="duration - fadeOutValue"
      @focus="emit('select')" @keydown="onHandleKeydown('fade-right', $event)"
      @pointerdown="onPointerDown('fade-right', $event)"
    >
      <div class="mt-1 h-2.5 w-2.5 rounded-full bg-accent1 shadow-[0_0_8px_var(--color-accent1)] transition-all group-hover:scale-125 group-hover:bg-white"></div>
    </div>
  </div>
</template>
