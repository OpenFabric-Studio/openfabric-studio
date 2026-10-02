// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n, setLocale } from '../../i18n'
import * as playback from '../../composables/audioPlayback'
import WaveformPlayer from './WaveformPlayer.vue'

vi.mock('../../composables/audioPlayback', () => ({
  claimPlayback: vi.fn(), releasePlaybackIfCurrent: vi.fn(), fetchAndComputePeaks: vi.fn(), peaksCache: new Map<string, number[]>(),
}))

let app: App | undefined
beforeEach(() => {
  setLocale('en')
  vi.mocked(playback.fetchAndComputePeaks).mockResolvedValue([.2, .7])
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) { this.dispatchEvent(new Event('pause')) })
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) { this.dispatchEvent(new Event('play')); return Promise.resolve() })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.resetAllMocks(); playback.peaksCache.clear() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount(src = '/first.wav') {
  const source = ref(src)
  const player = ref<InstanceType<typeof WaveformPlayer> | null>(null)
  app = createApp({ render: () => h(WaveformPlayer, { ref: player, src: source.value }) }).use(i18n)
  const container = document.createElement('div')
  document.body.append(container)
  app.mount(container)
  await settle()
  return { container, source, player }
}

it('exposes play without starting on mount or toggling already playing audio', async () => {
  const { container, player } = await mount()
  expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
  expect(typeof player.value?.play).toBe('function')
  await player.value?.play()
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce()
  expect(container.querySelector('button')?.getAttribute('aria-label')).toBe('Pause')
  await player.value?.play()
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce()
  expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled()
})

it('surfaces native playback rejection and provides a direct audio fallback', async () => {
  vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new DOMException('Codec unsupported', 'NotSupportedError'))
  const { container } = await mount()
  container.querySelector('button')?.click()
  await settle()
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(container.querySelector('a')?.getAttribute('href')).toBe('/first.wav')
  expect(playback.releasePlaybackIfCurrent).toHaveBeenCalledWith(container.querySelector('audio'))
})

it('pauses old audio and resets playing state when the sample URL changes', async () => {
  const { container, source } = await mount()
  container.querySelector('button')?.click()
  await settle()
  expect(container.querySelector('button')?.getAttribute('aria-label')).toBe('Pause')
  vi.mocked(HTMLMediaElement.prototype.pause).mockClear()
  source.value = '/second.wav'
  await settle()
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce()
  expect(container.querySelector('button')?.getAttribute('aria-label')).toBe('Play')
  expect(container.querySelector('audio')?.getAttribute('src')).toBe('/second.wav')
})

it('pauses and releases its audio on unmount', async () => {
  const { container } = await mount()
  const audio = container.querySelector('audio')
  app?.unmount(); app = undefined
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce()
  expect(playback.releasePlaybackIfCurrent).toHaveBeenCalledWith(audio)
})

it('ignores late playback rejection from a previous source', async () => {
  let rejectPlay: (reason: unknown) => void = () => { throw new Error('Not initialized') }
  vi.mocked(HTMLMediaElement.prototype.play).mockReturnValue(new Promise<void>((_resolve, reject) => { rejectPlay = reject }))
  const { container, source } = await mount()
  container.querySelector('button')?.click()
  source.value = '/second.wav'
  await settle()
  rejectPlay(new DOMException('Old request stopped', 'AbortError'))
  await settle()
  expect(container.querySelector('[role="alert"]')).toBeNull()
})

it('treats a pending play interrupted by another player as cancellation', async () => {
  let rejectPlay: (reason: unknown) => void = () => { throw new Error('Not initialized') }
  vi.mocked(HTMLMediaElement.prototype.play).mockReturnValue(new Promise<void>((_resolve, reject) => { rejectPlay = reject }))
  const { container } = await mount()
  container.querySelector('button')?.click()
  const audio = container.querySelector('audio')
  if (!audio) throw new Error('Missing audio')
  Object.defineProperty(audio, 'paused', { configurable: true, value: true })
  audio.pause()
  rejectPlay(new DOMException('Interrupted by another player', 'AbortError'))
  await settle()
  expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(container.querySelector('button')?.disabled).toBe(false)
})

it('exposes keyboard seeking and reports native media errors', async () => {
  const { container } = await mount()
  const audio = container.querySelector('audio')
  if (!audio) throw new Error('Missing audio')
  Object.defineProperty(audio, 'duration', { value: 20 })
  audio.dispatchEvent(new Event('loadedmetadata'))
  await settle()
  const slider = container.querySelector('[role="slider"]')
  slider?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
  expect(audio.currentTime).toBe(5)
  audio.dispatchEvent(new Event('error'))
  await settle()
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
})

it('reports actual playback transitions so pagination can retain the playing card', async () => {
  const states: boolean[] = []
  app = createApp({ render: () => h(WaveformPlayer, { src: '/retained.wav', onPlaying: (value: boolean) => states.push(value) }) }).use(i18n)
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  const audio = container.querySelector('audio')
  if (!audio) throw new Error('Missing audio')
  audio.dispatchEvent(new Event('play')); await settle()
  expect(states.at(-1)).toBe(true)
  audio.dispatchEvent(new Event('pause')); await settle(); expect(states.at(-1)).toBe(false)
  audio.dispatchEvent(new Event('play')); await settle()
  app.unmount(); app = undefined; expect(states.at(-1)).toBe(false)
})
