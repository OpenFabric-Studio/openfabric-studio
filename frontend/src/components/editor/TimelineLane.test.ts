// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createPinia } from 'pinia'
import TimelineLane from './TimelineLane.vue'
import type { TimelineLane as Lane } from '../../audio/timelineTypes'
import { defaultChannelSettings } from '../../audio/mixerEngine'
import { TestAudioBuffer } from '../../audio/testWebAudio'
import { i18n } from '../../i18n'

type WaveformContext = Pick<CanvasRenderingContext2D, 'setTransform' | 'clearRect' | 'createLinearGradient' | 'fillStyle' | 'beginPath' | 'moveTo' | 'lineTo' | 'closePath' | 'fill'>

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })

it.each([false, true])('samples the selected audio buffer using its own time domain (warp ready: %s)', async ready => {
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  const gradient: CanvasGradient = { addColorStop: () => {} }
  const context: WaveformContext = {
    setTransform: () => {}, clearRect: () => {}, createLinearGradient: () => gradient, fillStyle: '',
    beginPath: () => {}, moveTo: () => {}, lineTo: () => {}, closePath: () => {}, fill: () => {},
  }
  const descriptor = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, 'getContext')
  if (!descriptor) throw new Error('Missing canvas boundary')
  // This boundary double supports only 2D waveform operations, not browser rasterization.
  Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', { configurable: true, value: (kind: string) => kind === '2d' ? context : null })
  vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 100, 28))
  try {
    const lane: Lane = { id: 'audio', name: 'Audio', settings: defaultChannelSettings(), clips: [
      { id: 'clip', sourceLabel: 'Audio', sourceUrl: '/song.wav', timelineStart: 0, trimStart: 1, trimEnd: 4, warpEnabled: true, originalBpm: 240 },
    ] }
    const original = new AudioBuffer({ numberOfChannels: 1, length: 40000, sampleRate: 8000 })
    const warped = new AudioBuffer({ numberOfChannels: 1, length: 80000, sampleRate: 8000 })
    const selected = ready ? warped : original
    const data = selected.getChannelData(0)
    let minimum = Infinity, maximum = -Infinity
    vi.spyOn(selected, 'getChannelData').mockReturnValue(new Proxy(data, {
      get(target, key) {
        if (typeof key === 'string' && /^\d+$/.test(key)) {
          minimum = Math.min(minimum, Number(key))
          maximum = Math.max(maximum, Number(key))
        }
        return Reflect.get(target, key, target)
      },
    }))
    const buffers = new Map([['/song.wav', original]])
    if (ready) buffers.set('/song.wav_warp_240_120', warped)
    const container = document.body.appendChild(document.createElement('div'))
    app = createApp({ render: () => h(TimelineLane, {
      lane, selected: true, pxPerSecond: 100, buffers, snapCandidates: [], gridStepSec: 0.5,
      snapEnabled: false, selectedClipId: null, widthPx: 1000, level: { peak: 0, clipping: false, peakL: 0, peakR: 0 },
    }) }).use(createPinia()).use(i18n)
    app.mount(container)
    await nextTick()
    const factor = ready ? 2 : 1
    expect(minimum).toBe(8000 * factor)
    expect(maximum).toBeLessThan(32000 * factor)
    expect(maximum).toBeGreaterThan(31000 * factor)
  } finally { Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', descriptor) }
})

it('keeps the original source end editable while the stretched replacement is not yet available', async () => {
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  const lane = ref<Lane>({ id: 'piano', name: 'Piano', settings: defaultChannelSettings(), clips: [
    { id: 'clip', sourceLabel: 'Audio', sourceUrl: '/song.wav', timelineStart: 0, trimStart: 1, trimEnd: 4, warpEnabled: true, originalBpm: 240 },
  ] })
  const original = new AudioBuffer({ numberOfChannels: 1, length: 40000, sampleRate: 8000 })
  const container = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TimelineLane, {
    lane: lane.value, selected: true, pxPerSecond: 100, buffers: new Map([['/song.wav', original]]), snapCandidates: [],
    gridStepSec: 0.5, snapEnabled: false, selectedClipId: 'clip', widthPx: 1000,
    level: { peak: 0, clipping: false, peakL: 0, peakR: 0 },
    onTrimClip: (payload) => {
      const clip = lane.value.clips.find(item => item.id === payload.clipId)
      if (clip) Object.assign(clip, payload)
    },
  }) }).use(createPinia()).use(i18n)
  app.mount(container)
  await nextTick()
  const end = container.querySelector('[role="slider"][aria-label="Clip end trim"]')
  if (!(end instanceof HTMLElement)) throw new Error('Missing trim-end control')
  end.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }))
  await nextTick()
  expect(lane.value.clips[0]?.trimEnd).toBe(5)
  expect(Number(end.getAttribute('aria-valuemax'))).toBe(8)
})

it('gives the track name an accessible label and preserves focus selection and rename events', async () => {
  const lane: Lane = { id: 'piano', name: 'Piano', clips: [], settings: defaultChannelSettings() }
  const renamed: string[] = []
  let selections = 0
  const container = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TimelineLane, {
    lane, selected: false, pxPerSecond: 40, buffers: new Map(), snapCandidates: [], gridStepSec: 0.5,
    snapEnabled: true, selectedClipId: null, widthPx: 400, level: { peak: 0, clipping: false, peakL: 0, peakR: 0 },
    onRename: (name) => { renamed.push(name) }, onSelectLane: () => { selections++ },
  }) }).use(createPinia()).use(i18n)
  app.mount(container)
  const name = container.querySelector('input[aria-label="Track name"]')
  if (!(name instanceof HTMLInputElement)) throw new Error('Missing named track input')
  name.focus()
  name.value = 'Keys'
  name.dispatchEvent(new Event('input', { bubbles: true }))
  expect(selections).toBe(1)
  expect(renamed).toEqual(['Keys'])
})
