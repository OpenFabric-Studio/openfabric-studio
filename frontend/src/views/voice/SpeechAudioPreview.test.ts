// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import SpeechAudioPreview from './SpeechAudioPreview.vue'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import { speechWorkspaceEn } from '../../locales/speechWorkspace'

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 5; i++) await nextTick() }
async function mount() {
  const active = ref(true), source = ref('/api/source-one/audio')
  app = createApp({ render: () => h('div', [h(SpeechAudioPreview, { src: source.value, label: 'First source', active: active.value }), h(SpeechAudioPreview, { src: '/api/source-two/audio', label: 'Second source', active: active.value })]) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { speechWorkspace: speechWorkspaceEn } } }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  return { container, active, source }
}
function players(container: HTMLElement): HTMLAudioElement[] { return [...container.querySelectorAll('audio')] }
it('uses accessible native controls without fetching waveform or model data', async () => {
  const { container } = await mount()
  const first = players(container)[0]
  expect(first?.getAttribute('aria-label')).toBe('First source')
  expect(first?.hasAttribute('controls')).toBe(true)
  expect(first?.getAttribute('preload')).toBe('metadata')
})
it('shares playback ownership and releases only the current player', async () => {
  const { container } = await mount()
  const [first, second] = players(container)
  if (!first || !second) throw new Error('Missing source audio')
  const pauseFirst = vi.spyOn(first, 'pause').mockImplementation(() => undefined)
  const pauseSecond = vi.spyOn(second, 'pause').mockImplementation(() => undefined)
  first.dispatchEvent(new Event('play')); second.dispatchEvent(new Event('play'))
  expect(pauseFirst).toHaveBeenCalledTimes(1)
  first.dispatchEvent(new Event('pause'))
  const other = document.createElement('audio'); claimPlayback(other)
  expect(pauseSecond).toHaveBeenCalledTimes(1)
  releasePlaybackIfCurrent(other)
})
it('pauses and releases playback when hidden, and rejects a late play event', async () => {
  const { container, active } = await mount()
  const first = players(container)[0]
  if (!first) throw new Error('Missing source audio')
  const pause = vi.spyOn(first, 'pause').mockImplementation(() => undefined)
  first.dispatchEvent(new Event('play')); active.value = false; await settle()
  expect(pause).toHaveBeenCalledTimes(1)
  first.dispatchEvent(new Event('play'))
  expect(pause).toHaveBeenCalledTimes(2)
  const other = document.createElement('audio'); claimPlayback(other)
  expect(pause).toHaveBeenCalledTimes(2)
  releasePlaybackIfCurrent(other)
})
it('shows translated media errors and clears them when its source changes', async () => {
  const { container, source } = await mount()
  const first = players(container)[0]
  if (!first) throw new Error('Missing source audio')
  first.dispatchEvent(new Event('error')); await settle()
  expect(container.querySelector('[role=alert]')?.textContent).toBe('Could not play this audio. Try the preview again.')
  source.value = '/api/replacement/audio'; await settle()
  expect(container.querySelector('[role=alert]')).toBeNull()
})
it('pauses and releases ownership on teardown', async () => {
  const { container } = await mount()
  const first = players(container)[0]
  if (!first) throw new Error('Missing source audio')
  const pause = vi.spyOn(first, 'pause').mockImplementation(() => undefined)
  first.dispatchEvent(new Event('play')); app?.unmount(); app = undefined
  expect(pause).toHaveBeenCalledTimes(1)
  const other = document.createElement('audio'); claimPlayback(other)
  expect(pause).toHaveBeenCalledTimes(1)
  releasePlaybackIfCurrent(other)
})
