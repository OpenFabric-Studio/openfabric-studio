// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import VideoPreviewPlayer from './VideoPreviewPlayer.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks() })
it('pauses a detached preview and releases the shared playback slot', async () => {
  app = createApp({ render: () => h(VideoPreviewPlayer, { src: '/clip.mp4', label: 'Clip' }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')))
  const video = document.querySelector('video')
  if (!video) throw new Error('Missing video')
  const pause = vi.spyOn(video, 'pause')
  video.dispatchEvent(new Event('play'))
  app.unmount(); app = undefined
  expect(pause).toHaveBeenCalled()
  const other = document.createElement('audio')
  pause.mockClear(); claimPlayback(other)
  expect(pause).not.toHaveBeenCalled()
  releasePlaybackIfCurrent(other)
})
it('stops old media when a new export replaces it and shows a decode error fallback', async () => {
  const src = ref('/old.mp4')
  app = createApp({ render: () => h(VideoPreviewPlayer, { src: src.value, label: 'Export' }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')))
  const video = document.querySelector('video')
  if (!video) throw new Error('Missing video')
  const pause = vi.spyOn(video, 'pause')
  video.dispatchEvent(new Event('play'))
  src.value = '/new.mp4'; await nextTick()
  expect(pause).toHaveBeenCalled()
  video.dispatchEvent(new Event('error')); await nextTick()
  expect(document.querySelector('[role=alert]')).not.toBeNull()
  expect(document.querySelector('a')?.getAttribute('href')).toBe('/new.mp4')
})
