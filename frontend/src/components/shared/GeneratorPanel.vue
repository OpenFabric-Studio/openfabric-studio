<script lang="ts">
export type GeneratorPanelMode = 'docked' | 'hidden' | 'floating'
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const mode = defineModel<GeneratorPanelMode>({ default: 'docked' })
const { t } = useI18n()
const panelId = useId()
const panel = ref<HTMLElement | null>(null)
const heading = ref<HTMLElement | null>(null)
const showButton = ref<HTMLButtonElement | null>(null)
const storageKey = 'openfabric:ace-generator-panel'
interface PanelPosition { x: number; y: number }
interface PanelDrag {
  pointerId: number
  clientX: number
  clientY: number
  start: PanelPosition
  surface: HTMLElement
}
const position = ref<PanelPosition | null>(null)
const dragging = ref(false)
const floatingStyle = computed(() => mode.value === 'floating' && position.value
  ? { left: `${position.value.x}px`, top: `${position.value.y}px`, right: 'auto' }
  : undefined)
let drag: PanelDrag | null = null
let sizeObserver: ResizeObserver | undefined
let active = true
let focusGeneration = 0

function clampPosition(next: PanelPosition): PanelPosition {
  const bounds = panel.value?.getBoundingClientRect()
  const margin = 12
  const rawHeaderHeight = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-header-height'))
  const headerHeight = Number.isFinite(rawHeaderHeight) ? Math.max(0, rawHeaderHeight) : 0
  const minY = headerHeight + margin
  return {
    x: Math.min(Math.max(margin, window.innerWidth - (bounds?.width ?? 0) - margin), Math.max(margin, next.x)),
    y: Math.min(Math.max(minY, window.innerHeight - (bounds?.height ?? 0) - margin), Math.max(minY, next.y)),
  }
}

function reclampPosition() {
  if (!active || mode.value !== 'floating' || !panel.value) return
  const bounds = panel.value.getBoundingClientRect()
  position.value = clampPosition(position.value ?? { x: bounds.left, y: bounds.top })
}

function finishDrag() {
  const current = drag
  drag = null
  dragging.value = false
  if (!current) return
  try {
    if (current.surface.hasPointerCapture(current.pointerId)) current.surface.releasePointerCapture(current.pointerId)
  } catch { /* Capture may already be lost during browser cancellation or teardown. */ }
}

function beginDrag(event: PointerEvent) {
  if (mode.value !== 'floating' || !event.isPrimary || event.button !== 0 || drag) return
  if (!(event.currentTarget instanceof HTMLElement) || !(event.target instanceof Element)) return
  const interactive = event.target.closest('button, a, input, textarea, select, [contenteditable], [role="button"]')
  if (interactive && interactive !== heading.value) return
  if (panel.value?.querySelector('[role="dialog"][aria-modal="true"]')) return
  reclampPosition()
  if (!position.value) return
  try { event.currentTarget.setPointerCapture(event.pointerId) } catch { return }
  drag = { pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY, start: position.value, surface: event.currentTarget }
  dragging.value = true
  event.preventDefault()
  heading.value?.focus({ preventScroll: true })
}

function moveDrag(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointerId || mode.value !== 'floating') return
  position.value = clampPosition({ x: drag.start.x + event.clientX - drag.clientX, y: drag.start.y + event.clientY - drag.clientY })
}

function endDrag(event: PointerEvent) {
  if (drag?.pointerId === event.pointerId) finishDrag()
}

function moveWithKeyboard(event: KeyboardEvent) {
  if (event.defaultPrevented || mode.value !== 'floating' || event.altKey || event.ctrlKey || event.metaKey) return
  const delta = event.shiftKey ? 50 : 10
  let x = 0
  let y = 0
  if (event.key === 'ArrowLeft') x = -delta
  else if (event.key === 'ArrowRight') x = delta
  else if (event.key === 'ArrowUp') y = -delta
  else if (event.key === 'ArrowDown') y = delta
  else return
  event.preventDefault()
  finishDrag()
  reclampPosition()
  if (position.value) position.value = clampPosition({ x: position.value.x + x, y: position.value.y + y })
}

function onResize() {
  finishDrag()
  reclampPosition()
}

async function changeMode(next: GeneratorPanelMode) {
  finishDrag()
  mode.value = next
  const generation = ++focusGeneration
  await nextTick()
  if (!active || generation !== focusGeneration) return
  if (next === 'hidden') showButton.value?.focus()
  else if (next === 'floating') heading.value?.focus()
  else panel.value?.focus()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented || mode.value !== 'floating') return
  const currentPanel = panel.value
  if (!currentPanel || currentPanel.querySelector('[role="dialog"][aria-modal="true"]')) return
  if (!(event.target instanceof Node) || !currentPanel.contains(event.target)) return
  event.preventDefault()
  void changeMode('hidden')
}

onMounted(() => {
  try {
    const saved = localStorage.getItem(storageKey)
    if (saved === 'docked' || saved === 'hidden' || saved === 'floating') mode.value = saved
  } catch { /* Browser storage is optional. */ }
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', onResize)
  if (typeof ResizeObserver !== 'undefined' && panel.value) {
    sizeObserver = new ResizeObserver(onResize)
    sizeObserver.observe(panel.value)
    // The height cap can leave the panel unchanged when application chrome wraps.
    const appHeader = document.querySelector('header')
    if (appHeader) sizeObserver.observe(appHeader)
  }
  reclampPosition()
})
watch(mode, (value) => {
  finishDrag()
  try { localStorage.setItem(storageKey, value) } catch { /* Preserve working controls when storage is blocked. */ }
})
watch(mode, reclampPosition, { flush: 'post' })
onBeforeUnmount(() => {
  active = false
  ++focusGeneration
  finishDrag()
  sizeObserver?.disconnect()
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="contents">
    <div class="col-span-full flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-panel p-3">
      <p class="text-sm font-medium text-text">
        {{ t('acePage.generator') }}
        <span class="ml-2 text-xs font-normal text-text-dim">{{ t(`acePage.generator${mode === 'docked' ? 'Docked' : mode === 'hidden' ? 'Hidden' : 'Floating'}`) }}</span>
      </p>
      <div class="flex flex-wrap gap-2">
        <button ref="showButton" v-show="mode === 'hidden'" type="button" class="generator-control" :aria-controls="panelId" :aria-expanded="mode !== 'hidden'" @click="changeMode('docked')">{{ t('acePage.showGenerator') }}</button>
        <button v-show="mode === 'floating'" type="button" class="generator-control" :aria-controls="panelId" @click="changeMode('docked')">{{ t('acePage.dockGenerator') }}</button>
        <button v-show="mode !== 'floating'" type="button" class="generator-control" :aria-controls="panelId" :aria-expanded="mode === 'floating'" @click="changeMode('floating')">{{ t('acePage.floatGenerator') }}</button>
        <button v-show="mode !== 'hidden'" type="button" class="generator-control" :aria-controls="panelId" @click="changeMode('hidden')">{{ t('acePage.hideGenerator') }}</button>
      </div>
    </div>
    <section
      :id="panelId"
      ref="panel"
      v-show="mode !== 'hidden'"
      role="region"
      :aria-label="t('acePage.generator')"
      :aria-describedby="mode === 'floating' ? `${panelId}-hint` : undefined"
      tabindex="-1"
      class="generator-panel min-w-0 self-start rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent1"
      :class="mode === 'floating' ? 'generator-panel-floating' : 'generator-panel-docked'"
      :style="floatingStyle"
    >
      <div
        v-show="mode === 'floating'"
        class="generator-drag-surface shrink-0 border-b border-border p-3"
        :class="{ 'generator-drag-active': dragging }"
        @pointerdown="beginDrag"
        @pointermove="moveDrag"
        @pointerup="endDrag"
        @pointercancel="endDrag"
        @lostpointercapture="endDrag"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-sm font-semibold text-text">
            <button
              ref="heading"
              type="button"
              class="generator-drag-handle flex min-h-10 items-center gap-2 rounded px-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent1"
              :aria-label="t('acePage.moveGenerator')"
              :aria-describedby="`${panelId}-hint`"
              aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown Shift+ArrowLeft Shift+ArrowRight Shift+ArrowUp Shift+ArrowDown"
              @keydown="moveWithKeyboard"
            >
              <svg aria-hidden="true" width="12" height="18" viewBox="0 0 12 18" fill="currentColor"><circle cx="3" cy="3" r="1.5" /><circle cx="9" cy="3" r="1.5" /><circle cx="3" cy="9" r="1.5" /><circle cx="9" cy="9" r="1.5" /><circle cx="3" cy="15" r="1.5" /><circle cx="9" cy="15" r="1.5" /></svg>
              {{ t('acePage.generator') }}
            </button>
          </h2>
          <div class="flex flex-wrap gap-2">
            <button type="button" class="generator-control" @click="changeMode('docked')">{{ t('acePage.dockGenerator') }}</button>
            <button type="button" class="generator-control" aria-keyshortcuts="Escape" @click="changeMode('hidden')">{{ t('acePage.hideGenerator') }}</button>
          </div>
        </div>
        <p :id="`${panelId}-hint`" class="mt-1 text-xs text-text-dim">{{ t('acePage.floatingHint') }}</p>
      </div>
      <div class="generator-panel-body"><slot /></div>
    </section>
  </div>
</template>

<style scoped>
@reference "../../style.css";
.generator-control {
  @apply min-h-10 rounded-lg border border-border bg-panel-2 px-3 py-2 text-xs font-medium text-text transition-colors hover:border-accent1 hover:text-accent1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent1;
}
.generator-panel-body :deep(> div) {
  /* The wrapper owns positioning; the existing form stays at one tree position. */
  position: static;
}
@media (min-width: 1024px) {
  .generator-panel-docked {
    position: sticky;
    top: calc(var(--app-header-height, 0px) + 1rem);
  }
}
.generator-panel-floating {
  position: fixed;
  z-index: 50;
  top: calc(var(--app-header-height, 0px) + .75rem);
  right: 1rem;
  display: flex;
  flex-direction: column;
  width: min(480px, calc(100vw - 2rem));
  max-height: min(680px, calc(100dvh - var(--app-header-height, 0px) - 1.5rem));
  border: 1px solid var(--color-border);
  background: var(--color-panel);
  box-shadow: 0 16px 48px rgb(0 0 0 / .35);
}
.generator-panel-floating .generator-panel-body {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.generator-drag-surface {
  touch-action: none;
}
.generator-drag-surface, .generator-drag-handle {
  cursor: grab;
}
.generator-drag-active, .generator-drag-active .generator-drag-handle {
  cursor: grabbing;
  user-select: none;
}
</style>
