// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import EditorPage from './EditorPage.vue'
import { useEditorStore } from '../../stores/editor'
import { i18n } from '../../i18n'
import * as tracksApi from '../../api/tracks'
import { decodeStem } from '../../audio/mixerEngine'
import type { SavedTrack } from '../../api/contracts'

const audioClock = vi.hoisted(() => ({ currentTime: 0 }))
const engine = vi.hoisted(() => ({
  ensureGraph: vi.fn(), applySettings: vi.fn(), decodeAll: vi.fn(async () => new Map<string, AudioBuffer>()),
  play: vi.fn(async (): Promise<number | null> => 0.05), queuePass: vi.fn(), stop: vi.fn(), teardown: vi.fn(), render: vi.fn(),
  getLaneLevel: vi.fn(() => ({ peak: 0, clipping: false, peakL: 0, peakR: 0 })),
  getMasterLevel: vi.fn(() => ({ peak: 0, clipping: false, peakL: 0, peakR: 0 })),
}))
vi.mock('../../composables/useTimelineEngine', () => ({ useTimelineEngine: () => engine }))
vi.mock('../../composables/audioPlayback', () => ({ getSharedAudioCtx: () => audioClock }))
vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), uploadTrack: vi.fn() }))
vi.mock('../../audio/mixerEngine', async (original) => ({ ...await original<typeof import('../../audio/mixerEngine')>(), decodeStem: vi.fn() }))

let app: App | undefined
const frames = new Map<number, FrameRequestCallback>()
beforeEach(() => {
  frames.clear()
  audioClock.currentTime = 0
  engine.play.mockImplementation(async () => audioClock.currentTime + 0.05)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })

async function mountEditor() {
  let frameId = 0
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => { frames.delete(id) }))
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/editor', component: { render: () => null } }, { path: '/editor/:id', component: EditorPage, props: true }] })
  await router.push('/editor/new')
  app = createApp({ render: () => h(RouterView) })
  app.use(pinia).use(router).use(i18n)
  const container = document.createElement('div')
  document.body.append(container)
  app.mount(container)
  await nextTick()
  await nextTick()
  const store = useEditorStore(pinia)
  return { store, router, container }
}

function runFrame(contextTime: number): void {
  audioClock.currentTime = contextTime
  const frame = frames.entries().next().value
  if (!frame) throw new Error('Missing transport frame')
  frames.delete(frame[0])
  frame[1](0)
}

function transportButton(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector('button[aria-label="Play"], button[aria-label="Pause"]')
  if (!(button instanceof HTMLButtonElement)) throw new Error('Missing transport button')
  return button
}

async function settlePlayback(): Promise<void> { await nextTick(); await nextTick() }

it('updates graph lane IDs without restarting retained lane playback', async () => {
  const { store } = await mountEditor()
  expect(engine.ensureGraph).toHaveBeenCalledWith(store.project.lanes.map((lane) => lane.id))
  store.playing = true
  store.addLane()
  await nextTick()
  expect(engine.ensureGraph).toHaveBeenCalledWith(store.project.lanes.map((lane) => lane.id))
  expect(engine.play).not.toHaveBeenCalled()
})

it('holds the playhead until scheduled audio starts and completes a fully muted project at its clock end', async () => {
  const { store, container } = await mountEditor()
  store.project.lanes[0]?.clips.push({ id: 'muted', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 4, muted: true })
  store.playheadSec = 1
  transportButton(container).click()
  await settlePlayback()
  runFrame(0.025)
  expect(store.playheadSec).toBe(1)
  runFrame(1.05)
  expect(store.playheadSec).toBeCloseTo(2)
  runFrame(3.05)
  expect(store.playing).toBe(false)
  expect(store.playheadSec).toBe(4)
  expect(frames.size).toBe(0)
  expect(engine.stop).toHaveBeenCalled()
})

it('ends an empty project without depending on a source onended event', async () => {
  const { store, container } = await mountEditor()
  transportButton(container).click()
  await settlePlayback()
  runFrame(0.05)
  expect(store.playing).toBe(false)
  expect(frames.size).toBe(0)
})

it('pre-schedules a loop past the project end, advances its clock at the exact boundary and recovers after a stalled queued pass', async () => {
  const { store, container } = await mountEditor()
  store.project.lanes[0]?.clips.push({ id: 'short', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 1 })
  store.project.loopRegion = { start: 2, end: 4, enabled: true }
  store.playheadSec = 2
  await nextTick()
  transportButton(container).click()
  await settlePlayback()
  expect(engine.play).toHaveBeenLastCalledWith(store.project, expect.any(Map), 2, 4)
  expect(engine.queuePass).toHaveBeenLastCalledWith(store.project, expect.any(Map), 2, 4, 2.05)
  runFrame(1.5)
  expect(store.playing).toBe(true)
  expect(store.playheadSec).toBeCloseTo(3.45)
  runFrame(2.05)
  expect(store.playheadSec).toBeCloseTo(2)
  expect(engine.queuePass).toHaveBeenLastCalledWith(store.project, expect.any(Map), 2, 4, 4.05)
  runFrame(6.1)
  await settlePlayback()
  expect(engine.play).toHaveBeenCalledTimes(2)
  expect(store.playheadSec).toBe(2)
  expect(frames.size).toBe(1)
})

it('does not restart ticking or queue a loop after a delayed audio resume is paused', async () => {
  let release = (_time: number | null): void => { throw new Error('Not initialized') }
  engine.play.mockReturnValue(new Promise<number | null>((resolve) => { release = resolve }))
  const { store, container } = await mountEditor()
  store.project.loopRegion = { start: 0, end: 10, enabled: true }
  await nextTick()
  transportButton(container).click()
  await nextTick()
  transportButton(container).click()
  release(10.05)
  await settlePlayback()
  expect(store.playing).toBe(false)
  expect(frames.size).toBe(0)
  expect(engine.queuePass).not.toHaveBeenCalled()
})

it.each([null, 0.05])('does not let an earlier resume result %s replace the clock after a newer keyboard seek completes', async (oldResult) => {
  let release = (_time: number | null): void => { throw new Error('Not initialized') }
  engine.play.mockReturnValueOnce(new Promise<number | null>((resolve) => { release = resolve }))
  const { store, container } = await mountEditor()
  store.project.lanes[0]?.clips.push({ id: 'clip', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 4 })
  transportButton(container).click()
  await nextTick()
  audioClock.currentTime = 10
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
  await settlePlayback()
  const seekOffset = store.playheadSec
  release(oldResult)
  await settlePlayback()
  runFrame(10.025)
  expect(store.playheadSec).toBe(seekOffset)
  expect(store.playing).toBe(true)
  expect(frames.size).toBe(1)
  expect(engine.play).toHaveBeenCalledTimes(2)
})

it('returns to a playable paused state if the latest audio start remains cancelled after retry', async () => {
  engine.play.mockResolvedValue(null)
  const { store, container } = await mountEditor()
  transportButton(container).click(); await settlePlayback()
  expect(engine.play).toHaveBeenCalledTimes(2)
  expect(store.playing).toBe(false)
  expect(frames.size).toBe(0)
})

it.each([null, 0.05])('ignores cancelled-start retry result %s after a newer seek starts', async (oldResult) => {
  let release = (_time: number | null): void => { throw new Error('Not initialized') }
  engine.play.mockResolvedValueOnce(null).mockReturnValueOnce(new Promise<number | null>(resolve => { release = resolve }))
  const { store, container } = await mountEditor()
  store.project.lanes[0]?.clips.push({ id: 'clip', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 4 })
  transportButton(container).click(); await settlePlayback()
  audioClock.currentTime = 10
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await settlePlayback()
  const offset = store.playheadSec
  release(oldResult); await settlePlayback()
  runFrame(10.025)
  expect(store.playheadSec).toBe(offset)
  expect(store.playing).toBe(true)
  expect(frames.size).toBe(1)
  expect(engine.play).toHaveBeenCalledTimes(3)
})

it('keeps scheduled loop bounds during a drag and reschedules only once when the drag is released', async () => {
  const { store, container } = await mountEditor()
  store.project.loopRegion = { start: 0, end: 4, enabled: true }
  await nextTick()
  transportButton(container).click()
  await settlePlayback()
  const handle = container.querySelector('.cursor-ew-resize.right-0')
  if (!(handle instanceof HTMLElement)) throw new Error('Missing loop end handle')
  handle.dispatchEvent(new PointerEvent('pointerdown', { clientX: 160, bubbles: true }))
  window.dispatchEvent(new PointerEvent('pointermove', { clientX: 200 }))
  await nextTick()
  expect(store.project.loopRegion.end).toBe(5)
  expect(engine.play).toHaveBeenCalledTimes(1)
  runFrame(4.05)
  expect(store.playheadSec).toBeCloseTo(0)
  expect(engine.queuePass).toHaveBeenLastCalledWith(store.project, expect.any(Map), 0, 4, 8.05)
  window.dispatchEvent(new PointerEvent('pointerup'))
  await settlePlayback()
  expect(engine.play).toHaveBeenCalledTimes(2)
  expect(engine.play).toHaveBeenLastCalledWith(store.project, expect.any(Map), 0, 5)
})

it('releases loop drag ownership when leaving the editor so pointer events cannot edit another project', async () => {
  const { store, router, container } = await mountEditor()
  store.project.loopRegion = { start: 0, end: 4, enabled: true }
  await nextTick()
  const handle = container.querySelector('.cursor-ew-resize.right-0')
  if (!(handle instanceof HTMLElement)) throw new Error('Missing loop end handle')
  handle.dispatchEvent(new PointerEvent('pointerdown', { clientX: 160, bubbles: true }))
  await router.push('/editor')
  store.newProject()
  store.project.loopRegion = { start: 2, end: 8, enabled: true }
  window.dispatchEvent(new PointerEvent('pointermove', { clientX: 200 }))
  window.dispatchEvent(new PointerEvent('pointerup'))
  expect(store.project.loopRegion).toEqual({ start: 2, end: 8, enabled: true })
  expect(store.dirty).toBe(false)
})

it.each(['help', 'library'] as const)('keeps Space, Delete and arrows from changing the editor behind the %s dialog', async (dialog) => {
  vi.spyOn(tracksApi, 'listTracks').mockResolvedValue([])
  const { store, container } = await mountEditor()
  const lane = store.project.lanes[0]
  if (!lane) throw new Error('Missing editor lane')
  lane.clips.push({ id: 'selected', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 4 })
  store.selectedClipId = 'selected'
  store.playheadSec = 1
  await nextTick()
  const button = [...container.querySelectorAll('button')].find(target => dialog === 'help'
    ? target.title === i18n.global.t('editor.help.title') : target.textContent?.trim() === i18n.global.t('editor.addTrack'))
  if (!button) throw new Error(`Missing ${dialog} opener`)
  button.click()
  await settlePlayback()
  expect(document.querySelector('[role="dialog"][aria-modal="true"]')).not.toBeNull()
  const before = JSON.stringify(store.project)
  for (const key of [' ', 'Delete', 'ArrowRight', 'Home', 'End']) {
    window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  }
  // Dispatch directly to the editor too: an open dialog owns local clip controls.
  const clipTarget = container.querySelector('.select-none[tabindex="0"]')
  if (!(clipTarget instanceof HTMLElement)) throw new Error('Missing clip keyboard target')
  clipTarget.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }))
  await nextTick()
  expect(store.playing).toBe(false)
  expect(store.playheadSec).toBe(1)
  expect(JSON.stringify(store.project)).toBe(before)
  expect(store.selectedClipId).toBe('selected')
})

it('gives loop edges slider semantics, bounded Home/End and fine steps without moving the playhead', async () => {
  const { store, container } = await mountEditor()
  store.project.lanes[0]?.clips.push({ id: 'clip', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 10 })
  store.project.loopRegion = { start: 1, end: 3, enabled: true }
  store.playheadSec = 0.75
  await nextTick()
  const start = container.querySelector('[role="slider"][aria-label="Loop start"]')
  const end = container.querySelector('[role="slider"][aria-label="Loop end"]')
  if (!(start instanceof HTMLElement) || !(end instanceof HTMLElement)) throw new Error('Missing keyboard loop edges')
  expect(start.tabIndex).toBe(0)
  expect(end.tabIndex).toBe(0)
  const press = async (target: HTMLElement, key: string, modifiers: { altKey?: boolean; shiftKey?: boolean } = {}): Promise<void> => {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...modifiers })
    target.dispatchEvent(event)
    await nextTick()
    expect(event.defaultPrevented).toBe(true)
    expect(store.playheadSec).toBe(0.75)
  }
  await press(start, 'ArrowRight', { shiftKey: true })
  expect(store.project.loopRegion.start).toBeCloseTo(2.9)
  expect(Number(start.getAttribute('aria-valuemax'))).toBeCloseTo(2.9)
  await press(start, 'Home')
  expect(store.project.loopRegion.start).toBe(0)
  await press(end, 'End')
  expect(store.project.loopRegion.end).toBe(10)
  await press(end, 'ArrowLeft', { altKey: true })
  expect(store.project.loopRegion.end).toBeCloseTo(9.99)
  await press(end, 'Home')
  expect(store.project.loopRegion.end).toBeCloseTo(0.1)
})

it('ignores a dropped-audio upload that finishes after leaving the editor', async () => {
  let release: (track: SavedTrack) => void = () => { throw new Error('Not initialized') }
  const upload = new Promise<SavedTrack>((resolve) => { release = resolve })
  vi.mocked(tracksApi.uploadTrack).mockReturnValue(upload)
  const { store, router, container } = await mountEditor()
  const lane = container.querySelector('.relative.isolate')
  if (!(lane instanceof HTMLElement)) throw new Error('Missing audio drop zone')
  const transfer = new DataTransfer()
  transfer.items.add(new File(['audio'], 'song.wav', { type: 'audio/wav' }))
  const drop = new DragEvent('drop', { bubbles: true, dataTransfer: transfer })
  Object.defineProperty(drop, 'dataTransfer', { value: transfer })
  expect(drop.dataTransfer?.files.length).toBe(1)
  lane.dispatchEvent(drop)
  expect(tracksApi.uploadTrack).toHaveBeenCalledTimes(1)
  await router.push('/editor')
  store.error = 'Current session warning'
  release({ id: 1, short_id: 1, title: 'Dropped', created_at: '2026-10-01T10:00:00Z', model: 'upload', lyrics: '', seed: null, duration_ms: null, wall_ms: null, params: {}, filename: 'song.wav', audio_url: '/api/tracks/1/audio', abc_url: null, stems: null, midi: null })
  await nextTick()
  await nextTick()
  expect(decodeStem).not.toHaveBeenCalled()
  expect(store.error).toBe('Current session warning')
  expect(store.project.lanes.every((track) => track.clips.length === 0)).toBe(true)
})
