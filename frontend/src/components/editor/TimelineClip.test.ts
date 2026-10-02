// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createPinia } from 'pinia'
import TimelineClip from './TimelineClip.vue'
import type { Clip } from '../../audio/timelineTypes'
import { i18n } from '../../i18n'
import { TestAudioBuffer } from '../../audio/testWebAudio'
import { TRACK_COLORS } from '../../utils/trackColors'

let app: App | undefined
beforeEach(() => {
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })

async function mountClip(overrides: Partial<Clip> = {}, sourceDuration = 5, snapCandidates: number[] = []) {
  const clip = ref<Clip>({ id: 'clip', sourceLabel: 'Audio', timelineStart: 0, trimStart: 1, trimEnd: 5, warpEnabled: true, originalBpm: 240, ...overrides })
  const factor = clip.value.warpEnabled && clip.value.originalBpm ? clip.value.originalBpm / 120 : 1
  const buffer = new AudioBuffer({ numberOfChannels: 1, length: sourceDuration * factor * 8000, sampleRate: 8000 })
  let commits = 0
  let selections = 0
  const container = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TimelineClip, {
    clip: clip.value, buffer, sourceDuration, pxPerSecond: 100, gridStepSec: 0.5, snapEnabled: false,
    snapCandidates, selected: true, laneName: 'Piano', theme: TRACK_COLORS[0],
    onTrim: (payload) => { clip.value = { ...clip.value, ...payload } },
    onMove: (timelineStart) => { clip.value.timelineStart = timelineStart },
    onFade: (payload) => { clip.value = { ...clip.value, ...payload } },
    onDragEnd: () => { commits++ }, onSelect: () => { selections++ },
  }) }).use(createPinia()).use(i18n)
  app.mount(container)
  await nextTick()
  const handle = (label: string): HTMLElement => {
    const target = container.querySelector(`[role="slider"][aria-label="${label}"]`)
    if (!(target instanceof HTMLElement)) throw new Error(`Missing ${label} slider`)
    return target
  }
  return { clip, container, handle, commits: () => commits, selections: () => selections }
}

async function press(target: HTMLElement, key: string, modifiers: { altKey?: boolean; shiftKey?: boolean } = {}): Promise<KeyboardEvent> {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...modifiers })
  target.dispatchEvent(event)
  await nextTick()
  return event
}

it('exposes four labeled, focusable sliders whose values stay inside their real edge bounds', async () => {
  const { container, handle, selections } = await mountClip()
  expect(container.querySelectorAll('[role="slider"][tabindex="0"]')).toHaveLength(4)
  for (const label of ['Clip start trim', 'Clip end trim', 'Fade in', 'Fade out']) {
    const slider = handle(label)
    const value = Number(slider.getAttribute('aria-valuenow'))
    expect(value).toBeGreaterThanOrEqual(Number(slider.getAttribute('aria-valuemin')))
    expect(value).toBeLessThanOrEqual(Number(slider.getAttribute('aria-valuemax')))
    expect(slider.getAttribute('title')).toContain('Home and End')
    slider.focus()
  }
  expect(selections()).toBe(4)
})

it('clamps a stretched left trim at timeline zero and preserves the 0.05 second timeline minimum', async () => {
  const { clip, handle, commits } = await mountClip()
  const start = handle('Clip start trim')
  await press(start, 'ArrowLeft')
  expect(clip.value.timelineStart).toBe(0)
  expect(clip.value.trimStart).toBe(1)
  await press(start, 'End')
  expect(clip.value.trimStart).toBeCloseTo(4.975)
  expect((clip.value.trimEnd - clip.value.trimStart) * 2).toBeCloseTo(0.05)
  await press(start, 'Home')
  expect(clip.value.timelineStart).toBeCloseTo(0)
  expect(clip.value.trimStart).toBeCloseTo(1)
  expect(commits()).toBe(3)
})

it('uses original source bounds for end trimming and stops handled keys before they can seek the transport', async () => {
  const { clip, handle, container, commits } = await mountClip({ timelineStart: 2, trimEnd: 3 })
  const end = handle('Clip end trim')
  let bubbled = 0
  const onKey = (): void => { bubbled++ }
  window.addEventListener('keydown', onKey)
  try {
    const event = await press(end, 'ArrowRight', { altKey: true, shiftKey: true })
    expect(event.defaultPrevented).toBe(true)
    expect(clip.value.trimEnd).toBeCloseTo(3.02)
    await press(end, 'End')
    expect(clip.value.trimEnd).toBe(5)
    const body = container.querySelector('[tabindex="0"]')
    if (!(body instanceof HTMLElement)) throw new Error('Missing clip keyboard target')
    await press(body, 'ArrowRight', { shiftKey: true })
    expect(clip.value.trimEnd).toBe(5)
    await press(end, 'Home')
    expect(clip.value.trimEnd).toBeCloseTo(1.025)
    expect(bubbled).toBe(0)
    expect(commits()).toBe(4)
  } finally { window.removeEventListener('keydown', onKey) }
})

it('bounds the existing Shift-arrow end trim in the timeline domain when a clip is sped up', async () => {
  const { clip, container } = await mountClip({ originalBpm: 60, timelineStart: 2, trimStart: 1, trimEnd: 1.1 })
  const body = container.querySelector('[tabindex="0"]')
  if (!(body instanceof HTMLElement)) throw new Error('Missing clip keyboard target')
  await press(body, 'ArrowLeft', { shiftKey: true })
  expect((clip.value.trimEnd - clip.value.trimStart) * 0.5).toBeCloseTo(0.05)
})

it('adjusts fade edges with fine steps and Home/End while preserving their timeline limits', async () => {
  const { clip, handle } = await mountClip({ fadeInDuration: 0.2, fadeOutDuration: 0.2 })
  await press(handle('Fade in'), 'ArrowRight', { altKey: true, shiftKey: true })
  expect(clip.value.fadeInDuration).toBeCloseTo(0.24)
  await press(handle('Fade in'), 'Home')
  expect(clip.value.fadeInDuration).toBe(0)
  await press(handle('Fade in'), 'End')
  expect(clip.value.fadeInDuration).toBe(8)
  await press(handle('Fade out'), 'ArrowLeft', { altKey: true })
  expect(clip.value.fadeOutDuration).toBeCloseTo(0.21)
  await press(handle('Fade out'), 'End')
  expect(clip.value.fadeOutDuration).toBe(0)
  await press(handle('Fade out'), 'Home')
  expect(clip.value.fadeOutDuration).toBe(8)
})

it('reclamps a snapped pointer trim to timeline zero instead of moving a stretched clip before zero', async () => {
  const { clip, container } = await mountClip({ timelineStart: 0.02, trimEnd: 2 }, 5, [-0.01])
  const start = container.querySelector('.cursor-ew-resize.left-0')
  if (!(start instanceof HTMLElement)) throw new Error('Missing pointer trim handle')
  start.dispatchEvent(new PointerEvent('pointerdown', { clientX: 0, bubbles: true }))
  window.dispatchEvent(new PointerEvent('pointermove', { clientX: -50 }))
  window.dispatchEvent(new PointerEvent('pointerup'))
  await nextTick()
  expect(clip.value.timelineStart).toBeCloseTo(0)
  expect(clip.value.trimStart).toBeCloseTo(0.99)
})

it('leaves an out-of-source clip intact instead of creating a negative duration from its end control', async () => {
  const { clip, handle } = await mountClip({ trimStart: 6, trimEnd: 7 })
  await press(handle('Clip end trim'), 'End')
  expect(clip.value.trimStart).toBe(6)
  expect(clip.value.trimEnd).toBe(7)
  expect(handle('Clip end trim').getAttribute('aria-disabled')).toBe('true')
})

it('reports an end slider value within a shortened source without silently changing saved trims', async () => {
  const { clip, handle } = await mountClip({ warpEnabled: false, trimStart: 0, trimEnd: 4 }, 2)
  const slider = handle('Clip end trim')
  expect(Number(slider.getAttribute('aria-valuenow'))).toBeLessThanOrEqual(Number(slider.getAttribute('aria-valuemax')))
  expect(clip.value.trimEnd).toBe(4)
  await press(slider, 'End')
  expect(clip.value.trimEnd).toBe(2)
})
