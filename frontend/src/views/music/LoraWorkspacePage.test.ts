// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, onBeforeUnmount, type App } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { appRoutes } from '../../router'
import { i18n } from '../../i18n'
import * as adapters from '../../api/musicLora'
import * as training from '../../api/stableLora'
import type { StableLoraTrainingJob } from '../../api/generated'

const aceUnmounted = vi.hoisted(() => vi.fn())
vi.mock('./MusicPage.vue', () => ({ default: defineComponent({ render: () => h(RouterView) }) }))
vi.mock('../ace-step/LoraTrainingPage.vue', () => ({ default: defineComponent({ setup() { onBeforeUnmount(aceUnmounted); return () => h('h2', 'ACE training controls') } }) }))
vi.mock('./ModelLoraTraining.vue', () => ({ default: { template: '<section data-model-training></section>' } }))
vi.mock('../../api/musicLora', async original => ({ ...await original<typeof import('../../api/musicLora')>(), musicLoraCapabilities: vi.fn(), listMusicAdapters: vi.fn() }))
vi.mock('../../api/modules', () => ({ listModuleJobs: vi.fn(async () => []) }))
vi.mock('../../api/stableLora', () => ({ stableLoraStatus: vi.fn().mockResolvedValue({ supported: false, installed: false }), listStableLora: vi.fn().mockResolvedValue({ jobs: [], history_incomplete: false }), cancelStableLora: vi.fn() }))
let app: App | undefined
async function settle() { for (let n = 0; n < 12; n++) await nextTick(); if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0); else await new Promise<void>(resolve => setTimeout(resolve, 0)); await nextTick() }
async function mount(path = '/music/lora') {
  const router = createRouter({ history: createMemoryHistory(), routes: appRoutes })
  await router.push(path); await router.isReady()
  app = createApp({ render: () => h(RouterView) }).use(router).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle()
  return { node, router }
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(adapters.musicLoraCapabilities).mockResolvedValue({ engines: [{ engine: 'ace_step', loading: true, training: true, reason: 'native' }, { engine: 'stable_audio3', loading: true, training: true, reason: 'native' }, { engine: 'yue2', loading: true, training: false, reason: 'inference_only' }, { engine: 'heartmula', loading: false, training: false, reason: 'unsupported_runtime' }] })
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [], history_incomplete: false })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })

it('opens an explanatory home with one route tab for each supported music model', async () => {
  const { node } = await mount()
  const tabs = [...node.querySelectorAll('[role="tab"]')]
  expect(tabs.map(tab => tab.textContent?.trim())).toEqual(['Overview', 'ACE-Step', 'Stable Audio 3', 'YuE', 'HeartMuLa'])
  expect(tabs[0]?.getAttribute('aria-selected')).toBe('true')
  expect(node.textContent).toContain('Start with the sound you want to teach')
  expect(node.textContent).toContain('Gradient accumulation')
  expect(node.textContent).toContain('Checkpoint and export')
  expect(node.textContent).toContain('Reconnecting is not resuming')
  expect(node.querySelector('form')).toBeNull()
})

it('moves between routed tabs with arrow keys and preserves the visited ACE workflow', async () => {
  const { node, router } = await mount('/music/lora/ace-step')
  expect(node.textContent).toContain('ACE training controls')
  const ace = node.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
  const navigated = new Promise<void>(resolve => {
    const stop = router.afterEach(to => {
      if (to.path === '/music/lora/stable-audio3') { stop(); resolve() }
    })
  })
  ace?.focus(); ace?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true })); await settle()
  await navigated; await settle()
  expect(router.currentRoute.value.path).toBe('/music/lora/stable-audio3')
  expect(document.activeElement?.getAttribute('aria-selected')).toBe('true')
  expect(aceUnmounted).not.toHaveBeenCalled()
  await router.push('/music/lora/ace-step'); await settle()
  expect(node.textContent).toContain('ACE training controls')
  expect(aceUnmounted).not.toHaveBeenCalled()
})

it('preserves the legacy ACE training bookmark, query and anchor', async () => {
  const { node, router } = await mount('/ace-step/lora?dataset=retained#training')
  expect(router.currentRoute.value.path).toBe('/music/lora/ace-step')
  expect(router.currentRoute.value.query.dataset).toBe('retained')
  expect(router.currentRoute.value.hash).toBe('#training')
  expect(node.textContent).toContain('ACE training controls')
  expect(node.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('ACE-Step')
})


it('observes an active Stable Audio job while another model tab is open', async () => {
  vi.useFakeTimers()
  const request = { name: 'Evening piano', variant: 'small-music', samples: [{ track_id: 42, caption: 'Solo piano' }], max_steps: 1000, rank: 16, learning_rate: 0.0001, seed: 42 } as const
  const job: StableLoraTrainingJob = { id: 'a'.repeat(32), status: 'running', stage: 'training', created_at: 'now', request: { ...request, samples: [...request.samples] }, adapter: null, error_code: null }
  vi.mocked(training.listStableLora).mockResolvedValue({ jobs: [job], history_incomplete: false })
  const { node, router } = await mount('/music/lora/stable-audio3')
  expect(node.textContent).toContain('Evening piano')
  await router.push('/music/lora/heartmula'); for (let n = 0; n < 12; n++) await nextTick()
  const before = vi.mocked(training.listStableLora).mock.calls.length
  await vi.advanceTimersByTimeAsync(2500)
  expect(training.listStableLora).toHaveBeenCalledTimes(before + 1)
  expect(node.textContent).not.toContain('Evening piano')
  expect(training.cancelStableLora).not.toHaveBeenCalled()
})
