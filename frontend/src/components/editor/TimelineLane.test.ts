// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createPinia } from 'pinia'
import TimelineLane from './TimelineLane.vue'
import type { TimelineLane as Lane } from '../../audio/timelineTypes'
import { defaultChannelSettings } from '../../audio/mixerEngine'
import { TestAudioBuffer } from '../../audio/testWebAudio'
import { i18n } from '../../i18n'

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })

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
