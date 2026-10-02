// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n, setLocale } from '../../i18n'
import { claimPlayback, releasePlaybackIfCurrent } from '../../composables/audioPlayback'
import BatchABPlayer from './BatchABPlayer.vue'

let app: App | undefined
const playedSources: string[] = []
beforeEach(() => {
  setLocale('en'); playedSources.length = 0
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) { Object.defineProperty(this, 'paused', { configurable: true, value: true }); this.dispatchEvent(new Event('pause')) })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) { Object.defineProperty(this, 'paused', { configurable: true, value: false }); playedSources.push(this.getAttribute('src') ?? ''); this.dispatchEvent(new Event('play')); return Promise.resolve() })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let index = 0; index < 8; index++) await nextTick() }
async function mount() {
  const sources = ref(['/first.wav', '/second.wav'])
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(BatchABPlayer, { sources: sources.value, labels: ['A', 'B'], durationSec: 30 }) }).use(i18n)
  app.mount(node); await settle()
  return { node, sources }
}
function variant(node: HTMLElement, label: string): HTMLButtonElement {
  const button = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!button) throw new Error(`Missing variant ${label}`)
  return button
}
function playButton(node: HTMLElement): HTMLButtonElement {
  const button = node.querySelector('button[aria-label="Play"],button[aria-label="Pause"]') ?? [...node.querySelectorAll('button')].at(-1)
  if (!(button instanceof HTMLButtonElement)) throw new Error('Missing accessible playback control')
  return button
}
function audios(node: HTMLElement): [HTMLAudioElement, HTMLAudioElement] {
  const [first, second] = node.querySelectorAll('audio')
  if (!first || !second) throw new Error('Missing variants')
  return [first, second]
}

it('stops when another player claims playback and switches silently at the same position', async () => {
  const { node } = await mount(); const [first, second] = audios(node)
  playButton(node).click(); await settle()
  first.currentTime = 12.5; first.dispatchEvent(new Event('timeupdate'))
  const external = document.body.appendChild(document.createElement('audio'))
  claimPlayback(external); await settle()
  variant(node, 'B').click(); await settle()
  expect(second.currentTime).toBe(12.5)
  expect(playedSources).toEqual(['/first.wav'])
  expect(playButton(node).getAttribute('aria-label')).toBe('Play')
  playButton(node).click(); await settle()
  expect(playedSources).toEqual(['/first.wav', '/second.wav'])
  expect(playButton(node).getAttribute('aria-label')).toBe('Pause')
  releasePlaybackIfCurrent(external)
})

it('continues an explicit playing comparison when switching variants and ignores old media events', async () => {
  const { node } = await mount(); const [first, second] = audios(node)
  playButton(node).click(); await settle()
  first.currentTime = 8; first.dispatchEvent(new Event('timeupdate'))
  variant(node, 'B').click(); await settle()
  expect(second.currentTime).toBe(8)
  expect(playedSources).toEqual(['/first.wav', '/second.wav'])
  first.dispatchEvent(new Event('pause')); first.dispatchEvent(new Event('ended')); await settle()
  expect(playButton(node).getAttribute('aria-label')).toBe('Pause')
  expect(node.querySelector('input')?.value).toBe('8')
  second.dispatchEvent(new Event('ended')); await settle()
  expect(playButton(node).getAttribute('aria-label')).toBe('Play')
})

it('ignores late successful play after another player has interrupted its pending request', async () => {
  let finish: (() => void) | undefined
  vi.mocked(HTMLMediaElement.prototype.play).mockImplementation(function (this: HTMLMediaElement) { playedSources.push(this.getAttribute('src') ?? ''); return new Promise<void>(resolve => { finish = resolve }) })
  const { node } = await mount(); const [first] = audios(node)
  playButton(node).click(); await settle()
  const external = document.body.appendChild(document.createElement('audio'))
  claimPlayback(external); first.dispatchEvent(new Event('pause')); await settle()
  finish?.(); first.dispatchEvent(new Event('play')); await settle()
  expect(playButton(node).getAttribute('aria-label')).toBe('Play')
  variant(node, 'B').click(); await settle()
  expect(playedSources).toEqual(['/first.wav'])
  releasePlaybackIfCurrent(external)
})

it('ignores late rejection from the previous variant after the next variant starts', async () => {
  let fail: ((reason: unknown) => void) | undefined
  vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(function (this: HTMLMediaElement) { this.dispatchEvent(new Event('play')); return new Promise<void>((_resolve, reject) => { fail = reject }) })
  const { node } = await mount()
  playButton(node).click(); await settle(); variant(node, 'B').click(); await settle()
  fail?.(new DOMException('Previous request stopped', 'AbortError')); await settle()
  expect(playButton(node).getAttribute('aria-label')).toBe('Pause')
  expect(node.querySelector('[role="alert"]')).toBeNull()
})

it('invalidates playback when sources are replaced and ignores late completion after teardown', async () => {
  let finish: (() => void) | undefined
  vi.mocked(HTMLMediaElement.prototype.play).mockImplementation(function (this: HTMLMediaElement) { this.dispatchEvent(new Event('play')); return new Promise<void>(resolve => { finish = resolve }) })
  const { node, sources } = await mount(); const [first] = audios(node)
  playButton(node).click(); await settle()
  sources.value = ['/replacement.wav', '/second.wav']; await settle()
  finish?.(); await settle()
  expect(playButton(node).getAttribute('aria-label')).toBe('Play')
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/replacement.wav')
  const pause = vi.mocked(HTMLMediaElement.prototype.pause)
  expect(pause.mock.contexts).toContain(first)
  playButton(node).click(); await settle(); app?.unmount(); app = undefined
  finish?.(); await settle()
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2)
  expect(node.querySelector('audio')).toBeNull()
})

it('reports current playback failures without leaking internal errors and supports explicit retry', async () => {
  vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce(new DOMException('/private/audio', 'NotSupportedError'))
  const { node } = await mount(); playButton(node).click(); await settle()
  expect(playButton(node).getAttribute('aria-label')).toBe('Play')
  expect(node.querySelector('[role="alert"]')).not.toBeNull()
  expect(node.textContent).not.toContain('/private/audio')
  playButton(node).click(); await settle()
  expect(playButton(node).getAttribute('aria-label')).toBe('Pause')
  expect(node.querySelector('[role="alert"]')).toBeNull()
})

it('ignores a queued pause event after the same audio has explicitly resumed', async () => {
  const { node } = await mount(); const [first] = audios(node)
  playButton(node).click(); await settle(); first.pause(); await settle()
  playButton(node).click(); await settle()
  first.dispatchEvent(new Event('pause')); await settle()
  variant(node, 'B').click(); await settle()
  expect(playedSources).toEqual(['/first.wav', '/first.wav', '/second.wav'])
  expect(playButton(node).getAttribute('aria-label')).toBe('Pause')
})

it('downloads the current source without labeling every format MP3', async () => {
  let filename = '', url = ''
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { filename = this.download; url = this.getAttribute('href') ?? '' })
  const { node } = await mount(); variant(node, 'B').click(); await settle()
  const download = [...node.querySelectorAll('button')].find(item => item.textContent?.includes('Download'))
  if (!download) throw new Error('Missing download button')
  download.click()
  expect(url).toBe('/second.wav')
  expect(filename).toMatch(/^variant_2_\d+$/)
})
