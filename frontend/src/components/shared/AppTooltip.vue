<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { isWithinActiveDialog } from '../../composables/useDialogA11y'

const props = withDefaults(defineProps<{ text: string; enabled?: boolean; side?: 'bottom' | 'right'; press?: boolean }>(), { enabled: true, side: 'right', press: false })
const anchor = ref<HTMLElement | null>(null), tooltip = ref<HTMLElement | null>(null)
const id = useId()
const hovered = ref(false), focused = ref(false), pinned = ref(false), dismissed = ref(false)
const visible = computed(() => props.enabled && isWithinActiveDialog(anchor.value) && !dismissed.value && (hovered.value || focused.value || pinned.value))
const position = ref({ left: 8, top: 8 })
let alive = true, generation = 0
let hideTimer: ReturnType<typeof setTimeout> | undefined

function cancelHide() { if (hideTimer !== undefined) { clearTimeout(hideTimer); hideTimer = undefined } }
function enter() { cancelHide(); hovered.value = true; dismissed.value = false }
function leave() { cancelHide(); hideTimer = setTimeout(() => { hideTimer = undefined; hovered.value = false }, 100) }
function focus() { focused.value = true; dismissed.value = false }
function blur(event: FocusEvent) {
  if (event.relatedTarget instanceof Node && anchor.value?.contains(event.relatedTarget)) return
  focused.value = false; pinned.value = false
}
function dismiss() { cancelHide(); pinned.value = false; hovered.value = false; dismissed.value = true }
function click() {
  if (!props.press) { dismiss(); return }
  if (pinned.value) dismiss()
  else { pinned.value = true; dismissed.value = false }
}
function escape(event: KeyboardEvent) { if (!event.defaultPrevented && event.key === 'Escape' && visible.value) { event.preventDefault(); event.stopPropagation(); dismiss() } }
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !anchor.value?.contains(event.target) && !tooltip.value?.contains(event.target)) dismiss()
}
function place() {
  const target = anchor.value?.getBoundingClientRect(), content = tooltip.value?.getBoundingClientRect()
  if (!target || !content || !alive || !visible.value) return
  const maxLeft = Math.max(8, window.innerWidth - content.width - 8)
  const maxTop = Math.max(8, window.innerHeight - content.height - 8)
  position.value = {
    left: Math.max(8, Math.min(maxLeft, props.side === 'right' ? target.right + 8 : target.left + (target.width - content.width) / 2)),
    top: Math.max(8, Math.min(maxTop, props.side === 'right' ? target.top + (target.height - content.height) / 2 : target.bottom + 6)),
  }
}
function removeListeners() {
  window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true)
  window.removeEventListener('pointerdown', outside, true)
}
watch(visible, async open => {
  const request = ++generation; removeListeners()
  if (!open) return
  window.addEventListener('resize', place); window.addEventListener('scroll', place, true)
  window.addEventListener('pointerdown', outside, true)
  await nextTick()
  if (alive && request === generation && visible.value) place()
})
watch(() => props.text, async () => { const request = ++generation; await nextTick(); if (alive && request === generation) place() })
watch(() => props.enabled, enabled => { if (!enabled) dismiss() })
// Child listeners mount before the drawer's dialog listener, so Escape dismisses
// its visible tooltip before it closes the containing drawer.
onMounted(() => window.addEventListener('keydown', escape, true))
onBeforeUnmount(() => { alive = false; ++generation; cancelHide(); removeListeners(); window.removeEventListener('keydown', escape, true) })
</script>
<template>
  <span ref="anchor" class="block min-w-0" @mouseenter="enter" @mouseleave="leave" @focusin="focus" @focusout="blur" @click="click" @keydown="escape">
    <slot :described-by="visible ? id : undefined" />
  </span>
  <Teleport to="body">
    <span v-if="visible" :id="id" ref="tooltip" role="tooltip" class="fixed z-[80] rounded-lg border border-border bg-panel-2 px-3 py-2 text-xs leading-relaxed text-text shadow-lg" :style="{ left: `${position.left}px`, top: `${position.top}px`, maxWidth: 'min(240px, calc(100vw - 16px))' }" @mouseenter="enter" @mouseleave="leave">{{ text }}</span>
  </Teleport>
</template>
