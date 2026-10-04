// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import VideoPage from './VideoPage.vue'
import * as videosApi from '../../api/videos'
import * as tracksApi from '../../api/tracks'
vi.mock('../../api/localEngines', async original => ({ ...await original<typeof import('../../api/localEngines')>(),
  listLocalEngines: vi.fn().mockResolvedValue({ video_engine: 'ltx', video_preference: 'ltx', note: 'Song videos stay on LTX.', engines: [] }),
}))
vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn() }))
vi.mock('../../api/videos', async (original) => ({ ...await original<typeof import('../../api/videos')>(), listVideos: vi.fn(), otherWorkBusy: vi.fn(), listVideoProjects: vi.fn(), videoReadiness: vi.fn() }))
let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(tracksApi.listTracks).mockResolvedValue([])
  vi.mocked(videosApi.otherWorkBusy).mockResolvedValue(false)
  vi.mocked(videosApi.listVideos).mockResolvedValue({ videos: [] })
  vi.mocked(videosApi.listVideoProjects).mockResolvedValue({ projects: [] })
  vi.mocked(videosApi.videoReadiness).mockResolvedValue({ engine_ready: false, analysis_ready: true, ffmpeg_ready: true, overlay_ready: true, options: [], modes: ['cover', 'visualizer'], warnings: [] })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
async function flush() { for (let i = 0; i < 6; i++) await nextTick() }
async function mountVideo() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: VideoPage }] })
  await router.push('/')
  app = createApp({ render: () => h(VideoPage) })
  app.use(createPinia()).use(i18n).use(router)
  const container = document.createElement('div')
  document.body.append(container)
  app.mount(container)
  await nextTick()
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
