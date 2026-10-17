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
import { detectBpm } from '../../audio/bpmDetector'
import { TestAudioBuffer } from '../../audio/testWebAudio'
import * as exportsApi from '../../api/audioExports'
import * as versionsApi from '../../api/audioVersions'
import * as settingsApi from '../../api/audioSettings'
import { audioSettingsResponse } from '../settings/settingsTestFixtures'
import * as projectsApi from '../../api/projects'
import { timeStretchBuffer } from '../../audio/timeStretchEngine'
import type { TimelineProject } from '../../audio/timelineTypes'
import * as beatsApi from '../../api/beats'
import * as mashupsApi from '../../api/mashups'
import { beatDocument, beatJob, beatRequest } from '../music/beatTestFixtures'
import { mashupJob } from '../../stores/mashupTestFixtures'
import type { BeatJob } from '../../api/generated'

const audioClock = vi.hoisted(() => ({ currentTime: 0 }))
const engine = vi.hoisted(() => ({
  ensureGraph: vi.fn(), applySettings: vi.fn(), decodeAll: vi.fn(async () => new Map<string, AudioBuffer>()),
  play: vi.fn(async (_project: TimelineProject, _buffers: Map<string, AudioBuffer>, _from: number, _until?: number): Promise<number | null> => 0.05), queuePass: vi.fn(), stop: vi.fn(), teardown: vi.fn(),
  render: vi.fn<(project: TimelineProject, buffers: Map<string, AudioBuffer>, duration: number) => Promise<AudioBuffer>>(),
  getLaneLevel: vi.fn(() => ({ peak: 0, clipping: false, peakL: 0, peakR: 0 })),
  getMasterLevel: vi.fn(() => ({ peak: 0, clipping: false, peakL: 0, peakR: 0 })),
}))
vi.mock('../../composables/useTimelineEngine', async original => {
  const actual = await original<typeof import('../../composables/useTimelineEngine')>()
  return { useTimelineEngine: () => ({ ...engine, toScheduledClips: actual.useTimelineEngine().toScheduledClips }) }
})
vi.mock('../../composables/audioPlayback', () => ({ getSharedAudioCtx: () => audioClock }))
vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), uploadTrack: vi.fn(), saveTrack: vi.fn() }))
vi.mock('../../audio/mixerEngine', async (original) => ({ ...await original<typeof import('../../audio/mixerEngine')>(), decodeStem: vi.fn() }))
vi.mock('../../audio/bpmDetector', () => ({ detectBpm: vi.fn(async () => 120) }))
vi.mock('../../api/audioExports', () => ({ createAudioExport: vi.fn(), getAudioExport: vi.fn() }))
vi.mock('../../api/audioVersions', () => ({ listAudioVersions: vi.fn() }))
vi.mock('../../api/audioSettings', () => ({ getAudioSettings: vi.fn() }))
vi.mock('../../audio/timeStretchEngine', () => ({ timeStretchBuffer: vi.fn() }))
vi.mock('../../api/beats', async original => ({ ...await original<typeof import('../../api/beats')>(), getBeat: vi.fn(), listBeatJobs: vi.fn(), getBeatJob: vi.fn() }))
vi.mock('../../api/mashups', async original => ({ ...await original<typeof import('../../api/mashups')>(), getMashupJob: vi.fn() }))

let app: App | undefined
const frames = new Map<number, FrameRequestCallback>()
beforeEach(() => {
  frames.clear()
  audioClock.currentTime = 0
  engine.play.mockImplementation(async () => audioClock.currentTime + 0.05)
  engine.decodeAll.mockResolvedValue(new Map())
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  vi.mocked(detectBpm).mockResolvedValue(120)
  vi.mocked(settingsApi.getAudioSettings).mockResolvedValue(audioSettingsResponse())
  vi.mocked(tracksApi.saveTrack).mockResolvedValue({ id: 42, short_id: 42, model: 'editor', created_at: 'now', title: 'Mix', lyrics: '', seed: null, duration_ms: 1000, wall_ms: null, params: {}, filename: 'mix.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null })
  vi.mocked(versionsApi.listAudioVersions).mockResolvedValue({ track_id: 42, original_available: true, versions: [{ id: 'a'.repeat(32), track_id: 42, kind: 'original', status: 'done', created_at: 'now', audio_url: '/source.wav' }] })
  vi.mocked(exportsApi.createAudioExport).mockResolvedValue({ id: 'b'.repeat(32), track_id: 42, version_id: 'a'.repeat(32), format: 'mp3', status: 'done', created_at: 'now', settings: audioSettingsResponse().settings, audio_url: '/processed.mp3' })
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

function linkedBeatJob(): BeatJob {
  const job = beatJob({ status: 'done', phase: 'done', beat_revision: 1, request: { ...beatRequest(), expected_revision: 1 } })
  job.manifest = { beat_id: job.beat_id, beat_revision: 1, document_sha256: '1'.repeat(64), render_id: job.id,
    seed: 4471, renderer_version: 'beat-render-v1', algorithm_version: 'beat-events-v1', frames: 144000,
    musical_frames: 144000, peak: .5, kit_manifest_sha256: '2'.repeat(64), events_sha256: '3'.repeat(64),
    artifacts: [{ id: 'f'.repeat(32), kind: 'stem', lane_id: 'lane-0', sha256: '4'.repeat(64), byte_size: 1024, media_type: 'audio/wav', filename: 'stem.wav' }] }
  return job
}

function beatAction(container: HTMLElement, text: string): HTMLButtonElement {
  const button = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!(button instanceof HTMLButtonElement)) throw new Error('Missing beat action: ' + text)
  return button
}

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 12; index++) await Promise.resolve()
  await nextTick()
}

it('explicitly updates a pinned beat clip while preserving arrangement, mixer and undo', async () => {
  const { store, container } = await mountEditor()
  const lane = store.project.lanes[0]
  if (!lane) throw new Error('Missing lane')
  lane.settings.volume = .6
  lane.clips.push({ id: 'linked', type: 'audio', sourceLabel: 'My beat', sourceUrl: '/old.wav',
    timelineStart: 8, trimStart: .2, trimEnd: 2, fadeInDuration: .1, fadeOutDuration: .2, originalBpm: 120,
    beatLink: { beat_id: 'a'.repeat(32), beat_revision: 0, render_id: 'b'.repeat(32), lane_id: 'lane-0', occurrence_index: null } })
  store.selectedClipId = 'linked'
  store.snapshot()
  vi.mocked(beatsApi.getBeat).mockResolvedValue({ ...beatDocument(), revision: 1 })
  const job = linkedBeatJob()
  vi.mocked(beatsApi.listBeatJobs).mockResolvedValue({ jobs: [job] })
  vi.mocked(beatsApi.getBeatJob).mockResolvedValue(job)
  await nextTick()
  expect(container.textContent).toContain('Pinned beat revision 0')
  beatAction(container, 'Check beat updates').click()
  await flushPromises()
  expect(container.textContent).toContain('Current saved revision 1')
  expect(lane.clips[0]?.sourceUrl).toBe('/old.wav')
  beatAction(container, 'Update selected beat clip').click()
  await flushPromises()
  const clip = lane.clips[0]
  expect(clip?.sourceUrl).toContain('/artifacts/' + 'f'.repeat(32))
  expect(clip?.beatLink?.beat_revision).toBe(1)
  expect([clip?.timelineStart, clip?.trimStart, clip?.trimEnd, clip?.fadeInDuration, clip?.fadeOutDuration]).toEqual([8, .2, 2, .1, .2])
  expect(lane.settings.volume).toBe(.6)
  store.undo()
  expect(store.project.lanes[0]?.clips[0]?.sourceUrl).toBe('/old.wav')
  expect(store.project.lanes[0]?.clips[0]?.beatLink?.beat_revision).toBe(0)
})

it('ignores a late beat update check after the editor session changes', async () => {
  const { store, container } = await mountEditor()
  const lane = store.project.lanes[0]
  if (!lane) throw new Error('Missing lane')
  lane.clips.push({ id: 'linked', sourceLabel: 'Beat', sourceUrl: '/old.wav', timelineStart: 0, trimStart: 0, trimEnd: 2,
    beatLink: { beat_id: 'a'.repeat(32), beat_revision: 0, render_id: 'b'.repeat(32), lane_id: 'lane-0', occurrence_index: null } })
  store.selectedClipId = 'linked'
  const pending = deferred<ReturnType<typeof beatDocument>>()
  vi.mocked(beatsApi.getBeat).mockReturnValue(pending.promise)
  await nextTick()
  beatAction(container, 'Check beat updates').click()
  await nextTick()
  store.newProject()
  pending.resolve({ ...beatDocument(), revision: 1 })
  await flushPromises()
  expect(store.project.lanes.every(item => item.clips.length === 0)).toBe(true)
  expect(container.textContent).not.toContain('Current saved revision 1')
})

it.each(['trim', 'warp'] as const)('retains existing linked audio when the new render conflicts with its %s', async reason => {
  const { store, container } = await mountEditor()
  const lane = store.project.lanes[0]
  if (!lane) throw new Error('Missing lane')
  lane.clips.push({ id: 'linked', sourceLabel: 'Beat', sourceUrl: '/old.wav', timelineStart: 8, trimStart: 0,
    trimEnd: reason === 'trim' ? 4 : 2, originalBpm: 120, warpEnabled: reason === 'warp',
    beatLink: { beat_id: 'a'.repeat(32), beat_revision: 0, render_id: 'b'.repeat(32), lane_id: 'lane-0', occurrence_index: null } })
  store.selectedClipId = 'linked'
  vi.mocked(beatsApi.getBeat).mockResolvedValue({ ...beatDocument(), revision: 1, bpm: reason === 'warp' ? 180 : 120 })
  vi.mocked(beatsApi.listBeatJobs).mockResolvedValue({ jobs: [linkedBeatJob()] })
  vi.mocked(beatsApi.getBeatJob).mockResolvedValue(linkedBeatJob())
  await nextTick()
  beatAction(container, 'Check beat updates').click(); await flushPromises()
  beatAction(container, 'Update selected beat clip').click(); await flushPromises()
  expect(lane.clips[0]?.sourceUrl).toBe('/old.wav')
  expect(lane.clips[0]?.beatLink?.beat_revision).toBe(0)
  expect(container.textContent).toContain(reason === 'trim' ? 'new render is shorter' : 'Turn off clip tempo warp')
})

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

function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  let reject: (reason: Error) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>((release, fail) => { resolve = release; reject = fail })
  return { promise, resolve, reject }
}

async function pendingWarp() {
  const base = new AudioBuffer({ numberOfChannels: 1, length: 80000, sampleRate: 8000 })
  const warped = new AudioBuffer({ numberOfChannels: 1, length: 40000, sampleRate: 8000 })
  const stretching = deferred<AudioBuffer>()
  vi.mocked(decodeStem).mockResolvedValue(base)
  vi.mocked(timeStretchBuffer).mockReturnValue(stretching.promise)
  engine.render.mockResolvedValue(warped)
  const editor = await mountEditor()
  const lane = editor.store.project.lanes[0]
  if (!lane) throw new Error('Missing warp lane')
  lane.clips.push({ id: 'warped', sourceUrl: '/audio.wav', sourceLabel: 'Warped', timelineStart: 0,
    trimStart: 2, trimEnd: 8, warpEnabled: true, originalBpm: 60 })
  for (let index = 0; index < 8; index++) await nextTick()
  const exportAction = [...editor.container.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(exportAction instanceof HTMLButtonElement)) throw new Error('Missing warp export')
  return { ...editor, base, warped, stretching, exportAction }
}

it('waits for one shared warp before exporting the captured clip and tempo', async () => {
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { store, base, warped, stretching, exportAction } = await pendingWarp()
  exportAction.click()
  await settlePlayback(); await settlePlayback()
  expect(engine.render).not.toHaveBeenCalled()
  expect(exportAction.disabled).toBe(true)
  store.updateClip('warped', { trimStart: 3 })
  await settlePlayback(); await settlePlayback()
  expect(timeStretchBuffer).toHaveBeenCalledExactlyOnceWith(base, 2)
  stretching.resolve(warped)
  for (let index = 0; index < 20; index++) await nextTick()
  const call = engine.render.mock.calls[0]
  expect(call?.[0].lanes[0]?.clips[0]?.trimStart).toBe(2)
  expect(call?.[1].get('/audio.wav_warp_60_120')).toStrictEqual(warped)
  expect(call?.[2]).toBe(3)
  expect(exportsApi.createAudioExport).toHaveBeenCalledOnce()
})

it('does not publish wrong-tempo audio when the required stretch fails', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  const { container, stretching, exportAction } = await pendingWarp()
  exportAction.click(); await settlePlayback(); await settlePlayback()
  stretching.reject(new Error('Private engine failure /private/source.wav'))
  for (let index = 0; index < 20; index++) await nextTick()
  expect(engine.render).not.toHaveBeenCalled()
  expect(tracksApi.saveTrack).not.toHaveBeenCalled()
  expect(container.textContent).toContain('Could not prepare warped audio. Retry the export.')
  expect(container.textContent).not.toContain('/private/source.wav')
  expect(exportAction.disabled).toBe(false)
})

it('reschedules current playback when a pending warp becomes available', async () => {
  const { store, container, warped, stretching } = await pendingWarp()
  transportButton(container).click(); await settlePlayback()
  expect(engine.play).toHaveBeenCalledOnce()
  store.playheadSec = 1.25
  stretching.resolve(warped)
  for (let index = 0; index < 12; index++) await nextTick()
  expect(engine.play).toHaveBeenCalledTimes(2)
  expect(engine.play.mock.calls.at(-1)?.[2]).toBe(1.25)
  expect(engine.play.mock.calls.at(-1)?.[1].get('/audio.wav_warp_60_120')).toStrictEqual(warped)
})

it.each(['disable-warp', 'cached-tempo'] as const)('reschedules playback after a %s change without a new buffer', async change => {
  const { store, container, warped, stretching } = await pendingWarp()
  stretching.resolve(warped)
  for (let index = 0; index < 12; index++) await nextTick()
  transportButton(container).click(); await settlePlayback()
  store.playheadSec = 1
  if (change === 'disable-warp') store.updateClip('warped', { warpEnabled: false })
  else store.setBpm(60)
  for (let index = 0; index < 8; index++) await nextTick()
  expect(engine.play).toHaveBeenCalledTimes(2)
  expect(engine.play.mock.calls.at(-1)?.[2]).toBe(1)
})

it.each(['Mute clip', 'Solo clip'])('reschedules playback once when %s changes the audible clip selection', async label => {
  const { store, container, warped, stretching } = await pendingWarp()
  stretching.resolve(warped)
  for (let index = 0; index < 12; index++) await nextTick()
  store.project.lanes[0]?.clips.push({ id: 'other', type: 'midi', sourceLabel: 'Other',
    timelineStart: 0, trimStart: 0, trimEnd: 2, notes: [] })
  await settlePlayback()
  transportButton(container).click(); await settlePlayback()
  const action = container.querySelector(`button[aria-label="${label}"]`)
  if (!(action instanceof HTMLButtonElement)) throw new Error('Missing clip action')
  action.click()
  for (let index = 0; index < 8; index++) await nextTick()
  expect(engine.play).toHaveBeenCalledTimes(2)
})

it.each(['timelineStart', 'trimStart', 'trimEnd', 'fadeInDuration', 'fadeOutDuration'] as const)('reschedules a changed %s from the current playhead during playback', async field => {
  const { store, container } = await mountEditor()
  vi.mocked(decodeStem).mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 80000, sampleRate: 8000 }))
  store.project.lanes[0]?.clips.push({ id: 'timing', sourceUrl: '/audio.wav', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 10 })
  for (let index = 0; index < 12; index++) await nextTick()
  transportButton(container).click()
  await settlePlayback()
  expect(engine.play).toHaveBeenCalledOnce()
  runFrame(2.05)

  store.updateClip('timing', { [field]: field === 'trimEnd' ? 9 : 1 }, true)
  for (let index = 0; index < 12; index++) await nextTick()

  expect(engine.play).toHaveBeenCalledTimes(2)
  expect(engine.play.mock.lastCall?.[2]).toBeCloseTo(2)
})

it('adopts each completed warp while another source is still stretching', async () => {
  const second = deferred<AudioBuffer>()
  const { store, container, base, warped, stretching } = await pendingWarp()
  vi.mocked(timeStretchBuffer).mockImplementation(buffer => buffer.duration === base.duration ? stretching.promise : second.promise)
  vi.mocked(decodeStem).mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 96000, sampleRate: 8000 }))
  store.project.lanes[0]?.clips.push({ id: 'second', sourceUrl: '/second.wav', sourceLabel: 'Second',
    timelineStart: 0, trimStart: 0, trimEnd: 12, warpEnabled: true, originalBpm: 60 })
  for (let index = 0; index < 8; index++) await nextTick()
  transportButton(container).click(); await settlePlayback()
  stretching.resolve(warped)
  for (let index = 0; index < 12; index++) await nextTick()
  expect(engine.play).toHaveBeenCalledTimes(2)
  expect(engine.play.mock.calls.at(-1)?.[1].has('/second.wav_warp_60_120')).toBe(false)
  second.resolve(new AudioBuffer({ numberOfChannels: 1, length: 48000, sampleRate: 8000 }))
  for (let index = 0; index < 12; index++) await nextTick()
  expect(engine.play).toHaveBeenCalledTimes(3)
})

it('ignores a pending export stretch after another editor session takes over', async () => {
  const { store, warped, stretching, exportAction } = await pendingWarp()
  exportAction.click(); await settlePlayback(); await settlePlayback()
  store.newProject()
  stretching.resolve(warped)
  for (let index = 0; index < 20; index++) await nextTick()
  expect(engine.render).not.toHaveBeenCalled()
  expect(tracksApi.saveTrack).not.toHaveBeenCalled()
  expect(engine.play).not.toHaveBeenCalled()
})

it.each(['muted', 'solo-excluded', 'lane-muted', 'lane-solo-excluded'] as const)('does not let a %s clip with unavailable warped audio block export', async excluded => {
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { store, container } = await mountEditor()
  const lane = store.project.lanes[0]
  if (!lane) throw new Error('Missing excluded clip lane')
  lane.clips.push({ id: 'excluded', sourceUrl: '/missing.wav', sourceLabel: 'Excluded', timelineStart: 0,
    trimStart: 0, trimEnd: 10, warpEnabled: true, originalBpm: 60, muted: excluded === 'muted' })
  if (excluded === 'solo-excluded') lane.clips.push({ id: 'solo', type: 'midi', sourceLabel: 'Solo',
    timelineStart: 0, trimStart: 0, trimEnd: 1, solo: true, notes: [] })
  if (excluded === 'lane-muted') lane.settings.muted = true
  if (excluded === 'lane-solo-excluded') {
    const other = store.project.lanes[1]
    if (!other) throw new Error('Missing solo lane')
    other.settings.solo = true
  }
  vi.mocked(decodeStem).mockRejectedValue(new Error('Missing excluded source'))
  engine.render.mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  const exportAction = [...container.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(exportAction instanceof HTMLButtonElement)) throw new Error('Missing excluded clip export')
  exportAction.click()
  for (let index = 0; index < 20; index++) await nextTick()
  expect(engine.render).toHaveBeenCalledOnce()
  expect(timeStretchBuffer).not.toHaveBeenCalled()
  expect(exportsApi.createAudioExport).toHaveBeenCalledOnce()
})

it('retains loaded warp buffers for playback and export when base decoding finishes before stretching', async () => {
  const base = new AudioBuffer({ numberOfChannels: 1, length: 80000, sampleRate: 8000 })
  const warped = new AudioBuffer({ numberOfChannels: 1, length: 40000, sampleRate: 8000 })
  const decoding = deferred<Map<string, AudioBuffer>>(), stretching = deferred<AudioBuffer>()
  vi.mocked(decodeStem).mockResolvedValue(base)
  vi.mocked(timeStretchBuffer).mockReturnValue(stretching.promise)
  const { store, router, container } = await mountEditor()
  const saved = projectsApi.parseTimelineProject(store.project)
  saved.lanes[0]?.clips.push({ id: 'warped', sourceUrl: '/audio.wav', sourceLabel: 'Warped', timelineStart: 0, trimStart: 0, trimEnd: 10, warpEnabled: true, originalBpm: 60 })
  vi.spyOn(projectsApi, 'getProject').mockResolvedValue({ id: 1, name: 'Warped project', data: saved, created_at: 'now', updated_at: 'now' })
  engine.decodeAll.mockReturnValueOnce(decoding.promise)
  await router.push('/editor/1'); await settlePlayback(); await settlePlayback()
  decoding.resolve(new Map([['/audio.wav', base]])); await settlePlayback(); await settlePlayback()
  expect(timeStretchBuffer).toHaveBeenCalledWith(base, 2)
  stretching.resolve(warped); for (let index = 0; index < 12; index++) await nextTick()
  transportButton(container).click(); await settlePlayback()
  expect(engine.play.mock.calls.at(-1)?.[1].get('/audio.wav_warp_60_120')?.duration).toBe(5)
  engine.render.mockResolvedValue(warped)
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const exportAction = [...container.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(exportAction instanceof HTMLButtonElement)) throw new Error('Missing export')
  exportAction.click(); for (let index = 0; index < 20; index++) await nextTick()
  expect(engine.render.mock.calls.at(-1)?.[1].get('/audio.wav_warp_60_120')?.duration).toBe(5)
})

it.each(['undo', 'redo'] as const)('completes an owned export after %s restores the same editor session', async change => {
  const render = deferred<AudioBuffer>(); engine.render.mockReturnValue(render.promise)
  const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { store, container } = await mountEditor()
  store.setBpm(90)
  if (change === 'redo') store.undo()
  const exportAction = [...container.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(exportAction instanceof HTMLButtonElement)) throw new Error('Missing export action')
  exportAction.click(); await settlePlayback()
  store[change](); await settlePlayback()
  render.resolve(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  for (let index = 0; index < 20; index++) await nextTick()
  expect(exportsApi.createAudioExport).toHaveBeenCalledOnce()
  expect(download).toHaveBeenCalledOnce()
  expect(exportAction.disabled).toBe(false)
})

async function pickLibraryTrack(container: HTMLElement): Promise<void> {
  vi.spyOn(tracksApi, 'listTracks').mockResolvedValue([{ id: 1, short_id: 1, title: 'Picked track', created_at: 'now', model: 'upload', lyrics: '', seed: null, duration_ms: null, wall_ms: null, params: {}, filename: 'song.wav', audio_url: '/api/tracks/1/audio', abc_url: null, stems: null, midi: null }])
  const add = [...container.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('editor.addTrack'))
  if (!add) throw new Error('Missing add button')
  add.click(); await settlePlayback(); await settlePlayback()
  const pick = [...document.querySelectorAll('[role=dialog] button')].find(button => button.textContent?.trim() === i18n.global.t('library.fullMix'))
  if (!(pick instanceof HTMLButtonElement)) throw new Error('Missing library track')
  pick.click(); await settlePlayback()
}

it.each(['decode', 'bpm', 'decode-error'] as const)('ignores a library import after another project takes over during %s', async stage => {
  const decode = deferred<AudioBuffer>(), bpm = deferred<number | null>()
  const buffer = new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 })
  vi.mocked(decodeStem).mockReturnValue(stage === 'bpm' ? Promise.resolve(buffer) : decode.promise)
  vi.mocked(detectBpm).mockReturnValue(bpm.promise)
  const { store, container } = await mountEditor()
  await pickLibraryTrack(container)
  store.newProject(); store.error = 'Current project warning'
  if (stage === 'decode-error') decode.reject(new Error('Previous project failure'))
  else { decode.resolve(buffer); bpm.resolve(120) }
  await settlePlayback(); await settlePlayback()
  expect(store.project.lanes).toHaveLength(2)
  expect(store.project.lanes.every(lane => lane.clips.length === 0)).toBe(true)
  expect(store.error).toBe('Current project warning')
  expect(store.dirty).toBe(false)
})

it('ignores library decoding that resolves after leaving the editor', async () => {
  const decode = deferred<AudioBuffer>(); vi.mocked(decodeStem).mockReturnValue(decode.promise)
  const { store, router, container } = await mountEditor()
  await pickLibraryTrack(container)
  await router.push('/editor')
  decode.resolve(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  await settlePlayback(); await settlePlayback()
  expect(store.project.lanes).toHaveLength(2)
  expect(detectBpm).not.toHaveBeenCalled()
})

it('retains the captured export project and format after navigation without downloading into the next session', async () => {
  const render = deferred<AudioBuffer>(); engine.render.mockReturnValue(render.promise)
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:editor-export')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { store, router, container } = await mountEditor()
  store.projectName = 'Project A'; store.projectId = 10
  const exportButton = [...container.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(exportButton instanceof HTMLButtonElement)) throw new Error('Missing export button')
  exportButton.click(); await settlePlayback()
  const capturedProject = engine.render.mock.calls[0]?.[0]
  store.project.bpm = 90
  expect(capturedProject?.bpm).toBe(120)
  await router.push('/editor'); store.newProject(); store.projectName = 'Project B'; store.projectId = 20
  render.resolve(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  await settlePlayback(); await settlePlayback()
  expect(tracksApi.saveTrack).toHaveBeenCalledWith(expect.objectContaining({ title: 'Project A', params: { project_export: true, project_id: 10 } }), expect.any(Blob), 'wav')
  expect(download).not.toHaveBeenCalled()
})

it('keeps a canonical WAV and delegates selected MP3 processing to the durable backend export', async () => {
  engine.render.mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { container } = await mountEditor()
  const select = container.querySelector('select[aria-label="Export format"]')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing export format')
  select.value = 'mp3'; select.dispatchEvent(new Event('change', { bubbles: true }))
  const button = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(button instanceof HTMLButtonElement)) throw new Error('Missing export action')
  button.click(); for (let index = 0; index < 20; index++) await nextTick()
  expect(tracksApi.saveTrack).toHaveBeenCalledWith(expect.any(Object), expect.any(Blob), 'wav')
  expect(exportsApi.createAudioExport).toHaveBeenCalledWith(42, 'a'.repeat(32), 'mp3', undefined, { settings: audioSettingsResponse().settings })
})

it.each([
  { label: 'known track', extraSource: null, complete: true },
  { label: 'untracked source', extraSource: '/external.wav', complete: false },
  { label: 'unsafe track identifier', extraSource: '/api/tracks/9007199254740992/audio', complete: false },
])('records an explicit ancestry-complete marker for an editor export with $label', async ({ extraSource, complete }) => {
  engine.render.mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { store, container } = await mountEditor()
  const lane = store.project.lanes[0]
  if (!lane) throw new Error('Missing export lane')
  lane.clips.push({ id: 'tracked', sourceUrl: '/api/tracks/7/audio', sourceLabel: 'Saved source', timelineStart: 0, trimStart: 0, trimEnd: 1 })
  if (extraSource) lane.clips.push({ id: 'external', sourceUrl: extraSource, sourceLabel: 'Untracked source', timelineStart: 0, trimStart: 0, trimEnd: 1 })
  await nextTick()
  const button = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(button instanceof HTMLButtonElement)) throw new Error('Missing export action')
  button.click(); for (let index = 0; index < 20; index++) await nextTick()
  expect(vi.mocked(tracksApi.saveTrack).mock.lastCall?.[0].params).toMatchObject({ source_track_ids: [7], source_ancestry_complete: complete })
})

it.each(['matched', 'hash-mismatch', 'revision-mismatch', 'offline'] as const)('preserves verified beat parent lineage on editor export with %s', async state => {
  engine.render.mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { store, container } = await mountEditor()
  const lane = store.project.lanes[0]
  if (!lane) throw new Error('Missing export lane')
  const job = linkedBeatJob()
  if (!job.manifest) throw new Error('Missing render manifest')
  job.manifest.track_id = 42
  if (state === 'offline') vi.mocked(beatsApi.getBeatJob).mockRejectedValue(new Error('offline'))
  else vi.mocked(beatsApi.getBeatJob).mockResolvedValue(job)
  lane.clips.push({ id: 'tracked', sourceUrl: '/api/tracks/7/audio', sourceLabel: 'Saved source', timelineStart: 0, trimStart: 0, trimEnd: 1 },
    { id: 'beat', sourceLabel: 'Beat', sourceUrl: beatsApi.beatArtifactUrl(job.id, 'f'.repeat(32)),
      timelineStart: 0, trimStart: 0, trimEnd: 1,
      beatLink: { beat_id: job.beat_id, beat_revision: state === 'revision-mismatch' ? 0 : 1, render_id: job.id, lane_id: 'lane-0', occurrence_index: null },
      sourceArtifact: { artifact_id: 'f'.repeat(32), sha256: (state === 'hash-mismatch' ? '5' : '4').repeat(64) } })
  await nextTick()
  const button = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(button instanceof HTMLButtonElement)) throw new Error('Missing export action')
  button.click(); for (let index = 0; index < 24; index++) await nextTick()
  expect(vi.mocked(tracksApi.saveTrack).mock.lastCall?.[0].params).toMatchObject({
    source_track_ids: state === 'matched' ? [7, 42] : [7], source_ancestry_complete: state === 'matched',
  })
})

it.each(['matched', 'hash-mismatch', 'url-mismatch', 'job-mismatch', 'revision-mismatch', 'preview', 'offline'] as const)(
  'preserves the retained mashup parent on editor export with %s', async state => {
    engine.render.mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const { store, container } = await mountEditor()
    const lane = store.project.lanes[0]
    if (!lane) throw new Error('Missing export lane')
    const job = mashupJob()
    const manifest = job.manifest
    const artifact = manifest?.artifacts.find(item => item.kind === 'placement')
    if (!manifest || !artifact || !job.request) throw new Error('Missing mashup fixture')
    manifest.track_id = 42
    const requestedId = job.id
    const sourceUrl = mashupsApi.mashupArtifactUrl(requestedId, artifact.id)
    if (state === 'job-mismatch') job.id = 'e'.repeat(32)
    if (state === 'revision-mismatch') manifest.mashup_revision++
    if (state === 'preview') { job.kind = 'preview'; job.request.mode = 'preview'; manifest.mode = 'preview' }
    if (state === 'offline') vi.mocked(mashupsApi.getMashupJob).mockRejectedValue(new Error('original sources detached'))
    else vi.mocked(mashupsApi.getMashupJob).mockResolvedValue(job)
    lane.clips.push({ id: 'tracked', sourceUrl: '/api/tracks/7/audio', sourceLabel: 'Existing track', timelineStart: 0, trimStart: 0, trimEnd: 1 },
      { id: 'mashup', sourceLabel: 'Retained mashup placement', sourceUrl: state === 'url-mismatch' ? sourceUrl + '/extra' : sourceUrl,
        timelineStart: 0, trimStart: 0, trimEnd: 1,
        sourceArtifact: { artifact_id: artifact.id, sha256: state === 'hash-mismatch' ? '0'.repeat(64) : artifact.sha256 } })
    await nextTick()
    const button = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === i18n.global.t('editor.export'))
    if (!(button instanceof HTMLButtonElement)) throw new Error('Missing export action')
    button.click(); for (let index = 0; index < 28; index++) await nextTick()
    expect(vi.mocked(tracksApi.saveTrack).mock.lastCall?.[0].params).toMatchObject({
      source_track_ids: state === 'matched' ? [7, 42] : [7], source_ancestry_complete: state === 'matched',
    })
    expect(versionsApi.listAudioVersions).toHaveBeenCalledWith(42)
  })

it('does not download a completed durable export when its owned polling response arrives after navigation', async () => {
  engine.render.mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  const completed = { id: 'b'.repeat(32), track_id: 42, version_id: 'a'.repeat(32), format: 'mp3' as const, status: 'done' as const, created_at: 'now', settings: audioSettingsResponse().settings, audio_url: '/processed.mp3' }
  vi.mocked(exportsApi.createAudioExport).mockResolvedValue({ ...completed, status: 'queued', audio_url: null })
  const response = deferred<ReturnType<typeof exportsApi.getAudioExport> extends Promise<infer Result> ? Result : never>()
  vi.mocked(exportsApi.getAudioExport).mockReturnValue(response.promise)
  const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const { container, router } = await mountEditor()
  const action = [...container.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('editor.export'))
  if (!(action instanceof HTMLButtonElement)) throw new Error('Missing export action')
  action.click(); for (let index=0; index<20; index++) await settlePlayback()
  expect(exportsApi.getAudioExport).toHaveBeenCalledOnce()
  await router.push('/editor')
  response.resolve(completed)
  await settlePlayback(); await settlePlayback()
  expect(download).not.toHaveBeenCalled()
  expect(exportsApi.createAudioExport).toHaveBeenCalledOnce()
})
