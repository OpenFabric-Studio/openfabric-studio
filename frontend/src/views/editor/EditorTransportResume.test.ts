// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import EditorPage from './EditorPage.vue'
import { useEditorStore } from '../../stores/editor'
import * as audioPlayback from '../../composables/audioPlayback'
import { TestAudioBuffer, TestAudioContext } from '../../audio/testWebAudio'
import { i18n } from '../../i18n'

let app: App | undefined
const frames = new Map<number, FrameRequestCallback>()
beforeEach(() => {
  frames.clear(); TestAudioContext.instances = []
  vi.stubGlobal('AudioContext', TestAudioContext); vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  vi.spyOn(audioPlayback, 'getSharedAudioCtx').mockReturnValue(new AudioContext())
  let id = 0
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { frames.set(++id, callback); return id }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => { frames.delete(id) }))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount() {
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/editor', component: { render: () => null } }, { path: '/editor/:id', component: EditorPage, props: true }] })
  await router.push('/editor/new')
  app = createApp({ render: () => h(RouterView) }).use(pinia).use(router).use(i18n)
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  const store = useEditorStore(pinia)
  store.project.lanes[0]?.clips.push({ id: 'muted', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 4, muted: true })
  store.snapshot(); await settle()
  return { store, container }
}
function play(container: HTMLElement) {
  const button = container.querySelector<HTMLButtonElement>('button[aria-label="Play"]')
  if (!button) throw new Error('Missing transport play')
  button.click()
}
function frame(time: number) {
  const context = TestAudioContext.instances[0]
  if (!context) throw new Error('Missing audio context')
  context.currentTime = time
  const entry = frames.entries().next().value
  if (!entry) throw new Error('Missing transport frame')
  frames.delete(entry[0]); entry[1](0)
}

it('keeps real transport playable when a lane is added during delayed audio activation', async () => {
  const { store, container } = await mount()
  const context = TestAudioContext.instances[0]
  if (!context) throw new Error('Missing audio context')
  let release = (): void => {}
  context.resumeResult = new Promise<void>(resolve => { release = resolve })
  play(container); store.addLane(); await settle(); release(); await settle()
  expect(frames.size).toBe(1)
  frame(6.05)
  expect(store.playheadSec).toBeCloseTo(1)
  expect(store.playing).toBe(true)
})

it('reschedules the current project when undo removes a lane during delayed audio activation', async () => {
  const { store, container } = await mount()
  store.addLane(); await settle()
  const context = TestAudioContext.instances[0]
  if (!context) throw new Error('Missing audio context')
  let release = (): void => {}
  context.resumeResult = new Promise<void>(resolve => { release = resolve })
  play(container); store.undo(); await settle(); release(); await settle()
  expect(frames.size).toBe(1)
  frame(6.05)
  expect(store.playheadSec).toBeCloseTo(1)
  expect(store.playing).toBe(true)
  frame(9.05)
  expect(store.playing).toBe(false)
})
