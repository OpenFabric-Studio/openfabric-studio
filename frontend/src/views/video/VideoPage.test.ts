// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import VideoPage from './VideoPage.vue'
import * as videosApi from '../../api/videos'
import * as tracksApi from '../../api/tracks'
import * as scenesApi from '../../api/videoScenes'
import * as swapsApi from '../../api/characterSwaps'
import type { VideoSceneSource, VideoSceneCut } from '../../api/contracts'
import { parseCharacterSwapJob } from '../../api/contracts'
import { videoProjectFixture } from './videoFixtures'
vi.mock('../../api/localEngines', async original => ({ ...await original<typeof import('../../api/localEngines')>(),
  listLocalEngines: vi.fn().mockResolvedValue({ video_engine: 'ltx', video_preference: 'ltx', note: 'Song videos stay on LTX.', engines: [] }),
}))
vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn() }))
vi.mock('../../api/videos', async (original) => ({ ...await original<typeof import('../../api/videos')>(), listVideos: vi.fn(), otherWorkBusy: vi.fn(), listVideoProjects: vi.fn(), videoReadiness: vi.fn() }))
vi.mock('../../api/videoScenes', async original => ({ ...await original<typeof import('../../api/videoScenes')>(), sceneReadiness: vi.fn(), listSceneSources: vi.fn(), listSceneCuts: vi.fn(), previewSceneSource: vi.fn(), deleteSceneCut: vi.fn() }))
vi.mock('../../api/characterSwaps', async original => ({ ...await original<typeof import('../../api/characterSwaps')>(), characterSwapReadiness: vi.fn(), listCharacterSwaps: vi.fn() }))
const movie: VideoSceneSource = { id: 'a'.repeat(32), name: 'Movie.avi', origin: 'native', duration_sec: 120, width: 640, height: 480, fps: 24, has_audio: true, bytes: 1000, created_at: 'today' }
const scene: VideoSceneCut = { id: 'b'.repeat(32), source_id: movie.id, name: 'Selected scene', start_sec: 10, end_sec: 18, seconds: 8, status: 'ready', error_code: '', created_at: 'today', updated_at: 'today', width: 640, height: 480, has_audio: true, output_url: `/api/videos/scenes/sources/${movie.id}/cuts/${'b'.repeat(32)}/media`, output_sha256: '', bytes: 1000 }
let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(tracksApi.listTracks).mockResolvedValue([])
  vi.mocked(videosApi.otherWorkBusy).mockResolvedValue(false)
  vi.mocked(videosApi.listVideos).mockResolvedValue({ videos: [] })
  vi.mocked(videosApi.listVideoProjects).mockResolvedValue({ projects: [] })
  vi.mocked(videosApi.videoReadiness).mockResolvedValue({ engine_ready: false, analysis_ready: true, ffmpeg_ready: true, overlay_ready: true, options: [], modes: ['cover', 'visualizer'], warnings: [] })
  vi.mocked(scenesApi.sceneReadiness).mockResolvedValue({ ffmpeg_ready: true, native_picker_available: true })
  vi.mocked(scenesApi.listSceneSources).mockResolvedValue({ sources: [movie] })
  vi.mocked(scenesApi.listSceneCuts).mockResolvedValue({ cuts: [scene] })
  vi.mocked(scenesApi.previewSceneSource).mockResolvedValue({ id: 'c'.repeat(32), source_id: movie.id, start_sec: 0, duration_sec: 15, width: 640, height: 480, url: `/api/videos/scenes/sources/${movie.id}/previews/${'c'.repeat(32)}` })
  vi.mocked(swapsApi.characterSwapReadiness).mockResolvedValue({ configured: false, ffmpeg_ready: true })
  vi.mocked(swapsApi.listCharacterSwaps).mockResolvedValue({ jobs: [] })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals(); vi.useRealTimers() })
async function flush() { for (let i = 0; i < 6; i++) await nextTick() }
async function mountVideo(url = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: VideoPage }, { path: '/settings', component: { render: () => h('div') } }] })
  await router.push(url)
  app = createApp({ render: () => h(VideoPage) })
  app.use(createPinia()).use(i18n).use(router)
  const container = document.createElement('div')
  document.body.append(container)
  app.mount(container)
  await nextTick()
  return router
}
function action(selector: string): HTMLButtonElement {
  const button = document.querySelector(selector)
  if (!(button instanceof HTMLButtonElement)) throw new Error(`Missing ${selector}`)
  return button
}
function panel(id: string): HTMLElement {
  const element = document.getElementById(`video-process-${id}`)
  if (!(element instanceof HTMLElement)) throw new Error(`Missing workflow ${id}`)
  return element
}
it('does not restart video polling when initial loading finishes after unmount', async () => {
  let release: (value: { videos: [] }) => void = () => { throw new Error('Not initialized') }
  vi.mocked(videosApi.listVideos).mockReturnValue(new Promise((resolve) => { release = resolve }))
  await mountVideo()
  app?.unmount()
  app = undefined
  release({ videos: [] })
  await flush()
  await vi.advanceTimersByTimeAsync(6000)
  expect(videosApi.listVideos).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})
it('keeps only one video refresh pending while a request is slow', async () => {
  await mountVideo()
  await flush()
  vi.mocked(videosApi.listVideos).mockClear()
  let release: (value: { videos: [] }) => void = () => { throw new Error('Not initialized') }
  vi.mocked(videosApi.listVideos).mockReturnValue(new Promise((resolve) => { release = resolve }))
  await vi.advanceTimersByTimeAsync(6000)
  expect(videosApi.listVideos).toHaveBeenCalledTimes(1)
  app?.unmount()
  app = undefined
  release({ videos: [] })
  await flush()
  expect(vi.getTimerCount()).toBe(0)
})
it('shows one workflow at a time and loads other tools only when requested', async () => {
  await mountVideo()
  await flush()
  expect(document.querySelectorAll('[data-video-process]')).toHaveLength(3)
  expect(panel('create').hidden).toBe(false)
  expect(panel('cut').hidden).toBe(true)
  expect(panel('replace').hidden).toBe(true)
  expect(scenesApi.sceneReadiness).not.toHaveBeenCalled()
  expect(swapsApi.characterSwapReadiness).not.toHaveBeenCalled()
  action('[data-video-process="cut"]').click(); await flush(); await flush()
  expect(panel('create').hidden).toBe(true)
  expect(panel('cut').hidden).toBe(false)
  expect(panel('replace').hidden).toBe(true)
  expect(scenesApi.sceneReadiness).toHaveBeenCalledOnce()
  expect(swapsApi.characterSwapReadiness).not.toHaveBeenCalled()
})
it('supports keyboard navigation through workflows with one tab stop', async () => {
  await mountVideo(); await flush()
  const create = action('[data-video-process="create"]')
  create.focus(); create.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true })); await flush()
  const replace = action('[data-video-process="replace"]')
  expect(replace.getAttribute('aria-selected')).toBe('true')
  expect(document.activeElement).toBe(replace)
  expect(document.querySelectorAll('[data-video-process][tabindex="0"]')).toHaveLength(1)
  replace.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await flush()
  expect(document.activeElement).toBe(create)
  expect(create.getAttribute('aria-selected')).toBe('true')
})
it('hands a completed local cut into replacement and focuses its heading', async () => {
  await mountVideo(); await flush()
  action('[data-video-process="cut"]').click(); await flush(); await flush()
  const use = document.querySelector('[data-scene-use]')
  if (!(use instanceof HTMLButtonElement)) throw new Error('Missing use action')
  use.click(); await flush(); await flush()
  expect(document.querySelector('[data-swap-chosen-scene]')?.textContent).toContain(scene.name)
  expect(panel('cut').hidden).toBe(true)
  expect(panel('replace').hidden).toBe(false)
  expect(panel('create').hidden).toBe(true)
  expect(document.querySelector<HTMLInputElement>('[data-swap-seconds]')?.value).toBe('2')
  expect(document.querySelector('[data-video-workspace-main]')).not.toBeNull()
  expect(document.activeElement?.id).toBe('character-swap-heading')
})
it('clears a deleted cut from replacement without deleting prepared swaps', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.mocked(scenesApi.deleteSceneCut).mockResolvedValue({ source_id: scene.source_id, deleted: scene.id })
  const id = 'd'.repeat(32), base = `/api/videos/character-swaps/${id}`
  const prepared = parseCharacterSwapJob({ id, name: 'Retained prepared swap', status: 'prepared', created_at: 'today', updated_at: 'today', start_sec: 0, seconds: 2, source_name: 'prepared.mp4', image_name: 'character.png', source_duration_sec: 8, width: 640, height: 480, frames: 48, has_audio: true, seed: 42, source_url: `${base}/source`, clip_url: `${base}/clip`, image_url: `${base}/image` })
  vi.mocked(swapsApi.listCharacterSwaps).mockResolvedValue({ jobs: [prepared] })
  await mountVideo(); await flush()
  action('[data-video-process="cut"]').click(); await flush(); await flush()
  action('[data-scene-use]').click(); await flush(); await flush()
  expect(document.querySelector('[data-swap-chosen-scene]')?.textContent).toContain(scene.name)
  action('[data-video-process="cut"]').click(); await flush()
  action('[data-scene-delete]').click(); await flush(); await flush()
  expect(document.querySelector('[data-swap-chosen-scene]')).toBeNull()
  expect(document.querySelector('[data-swap-video]')).not.toBeNull()
  expect(document.querySelector(`[data-swap-job="${prepared.id}"]`)?.textContent).toContain(prepared.name)
  expect(document.querySelector(`[src="${prepared.clip_url}"]`)).not.toBeNull()
})
it('keeps a chosen cut when a different movie cut with the same ID is deleted', async () => {
  const otherMovie = { ...movie, id: 'f'.repeat(32), name: 'Other movie' }
  const otherCut = { ...scene, source_id: otherMovie.id, name: 'Different movie scene', output_url: `/api/videos/scenes/sources/${otherMovie.id}/cuts/${scene.id}/media` }
  vi.mocked(scenesApi.listSceneSources).mockResolvedValue({ sources: [movie, otherMovie] })
  vi.mocked(scenesApi.listSceneCuts).mockImplementation(async id => ({ cuts: [id === movie.id ? scene : otherCut] }))
  vi.mocked(scenesApi.previewSceneSource).mockImplementation(async id => ({ id: 'c'.repeat(32), source_id: id, start_sec: 0, duration_sec: 15, width: 640, height: 480, url: `/api/videos/scenes/sources/${id}/previews/${'c'.repeat(32)}` }))
  vi.mocked(scenesApi.deleteSceneCut).mockResolvedValue({ source_id: otherMovie.id, deleted: scene.id })
  vi.stubGlobal('confirm', vi.fn(() => true))
  await mountVideo(); await flush()
  action('[data-video-process="cut"]').click(); await flush(); await flush()
  action('[data-scene-use]').click(); await flush(); await flush()
  action('[data-video-process="cut"]').click(); await flush()
  const select = document.querySelector('[data-scene-source]')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing movie selector')
  select.value = otherMovie.id; select.dispatchEvent(new Event('change', { bubbles: true })); await flush(); await flush()
  action('[data-scene-delete]').click(); await flush(); await flush()
  expect(document.querySelector('[data-swap-chosen-scene]')?.textContent).toContain(scene.name)
})
it('preserves unfinished cutter and replacement inputs across workflow changes', async () => {
  await mountVideo(); await flush()
  action('[data-video-process="cut"]').click(); await flush(); await flush()
  const out = document.querySelector<HTMLInputElement>('[data-scene-out]')
  if (!out) throw new Error('Missing out point')
  out.value = '11'; out.dispatchEvent(new Event('input', { bubbles: true })); await flush()
  action('[data-video-process="replace"]').click(); await flush(); await flush()
  const seconds = document.querySelector<HTMLInputElement>('[data-swap-seconds]')
  if (!seconds) throw new Error('Missing swap duration')
  seconds.value = '4'; seconds.dispatchEvent(new Event('input', { bubbles: true })); await flush()
  action('[data-video-process="create"]').click(); await flush()
  action('[data-video-process="cut"]').click(); await flush()
  expect(document.querySelector<HTMLInputElement>('[data-scene-out]')?.value).toBe('11')
  action('[data-video-process="replace"]').click(); await flush()
  expect(document.querySelector<HTMLInputElement>('[data-swap-seconds]')?.value).toBe('4')
  expect(scenesApi.sceneReadiness).toHaveBeenCalledOnce()
  expect(swapsApi.characterSwapReadiness).toHaveBeenCalledOnce()
})
it('pauses a preview before hiding its workflow', async () => {
  await mountVideo(); await flush()
  action('[data-video-process="cut"]').click(); await flush(); await flush()
  action('[data-scene-seek]').click(); await flush(); await flush()
  const player = document.querySelector<HTMLVideoElement>('[data-scene-preview]')
  if (!player) throw new Error('Missing scene preview')
  const pause = vi.spyOn(player, 'pause')
  action('[data-video-process="create"]').click(); await flush()
  expect(pause).toHaveBeenCalled()
  expect(player.isConnected).toBe(true)
})
it('opens saved-project deep links in creation and keeps workflow history navigable', async () => {
  const project = videoProjectFixture()
  vi.mocked(videosApi.listVideoProjects).mockResolvedValue({ projects: [project] })
  vi.spyOn(videosApi, 'getVideoProject').mockResolvedValue(project)
  const router = await mountVideo(`/?project=${project.id}`); await flush(); await flush()
  expect(panel('create').hidden).toBe(false)
  action('[data-video-process="cut"]').click(); await flush(); await flush()
  await vi.waitFor(() => expect(router.currentRoute.value.query.workflow).toBe('cut'))
  await router.push(`/?project=${project.id}`); await flush(); await flush()
  expect(panel('create').hidden).toBe(false)
})
