// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { appRoutes } from '../../router'
import { useOrchestratorStore } from '../../stores/orchestrator'
import * as api from '../../api/orchestrator'
import { i18n, setLocale } from '../../i18n'
import type { ModelId, OrchestratorStatus } from '../../types'

vi.mock('../../api/orchestrator', () => ({ getStatus: vi.fn(), switchModel: vi.fn(), stopActive: vi.fn() }))
vi.mock('../ace-step/AceStepPage.vue', () => ({ default: defineComponent({ render: () => h('h2', 'ACE generator') }) }))
vi.mock('../yue2/Yue2Page.vue', () => ({ default: defineComponent({ render: () => h('h2', 'YuE generator') }) }))
vi.mock('./CloudMusicPanel.vue', () => ({ default: defineComponent({ render: () => h('h2', 'Cloud generator') }) }))
vi.mock('./MusicQueuePage.vue', () => ({ default: defineComponent({ render: () => h('h2', 'Create & queue') }) }))
vi.mock('./LocalMusicPanel.vue', () => ({ default: defineComponent({ props: { engine: { type: String, required: true } }, setup: props => () => h('h2', `${props.engine} generator`) }) }))
vi.mock('../settings/SettingsPage.vue', () => ({ default: defineComponent({ render: () => h('h1', 'Settings') }) }))

let app: App | undefined
function snapshot(active: ModelId | null = null): OrchestratorStatus {
  return { active_model: active, models: {
    ace_step: { id: 'ace_step', label: 'ACE-Step 1.5', status: active === 'ace_step' ? 'running' : 'stopped', error: null },
    yue2: { id: 'yue2', label: 'YuE2-3B', status: active === 'yue2' ? 'running' : 'stopped', error: null },
  } }
}
beforeEach(() => {
  vi.clearAllMocks(); setLocale('en')
  vi.mocked(api.getStatus).mockResolvedValue(snapshot())
  vi.mocked(api.switchModel).mockImplementation(async id => snapshot(id))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); setActivePinia(undefined) })
async function settle() { for (let i = 0; i < 8; i++) await nextTick(); await new Promise<void>(resolve => setTimeout(resolve, 0)); await nextTick() }
async function mount(path = '/music/ace-step', active: ModelId | null = null) {
  const pinia = createPinia(); setActivePinia(pinia)
  const store = useOrchestratorStore(pinia); store._applySnapshot(snapshot(active))
  const router = createRouter({ history: createMemoryHistory(), routes: appRoutes })
  await router.push(path); await router.isReady()
  app = createApp({ render: () => h(RouterView) }).use(pinia).use(router).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle()
  return { router, store, node }
}
function modelButton(node: HTMLElement, id: ModelId): HTMLButtonElement {
  const button = node.querySelector<HTMLButtonElement>(`button[data-model="${id}"]`)
  if (!button) throw new Error(`Missing model selector: ${id}`)
  return button
}

it('opens the queue at Music without automatically starting an engine', async () => {
  const { node } = await mount('/music')
  expect(node.querySelector('h1')?.textContent).toBe('Music')
  expect(modelButton(node, 'ace_step').getAttribute('aria-pressed')).toBe('false')
  expect(modelButton(node, 'yue2').getAttribute('aria-pressed')).toBe('false')
  expect(node.textContent).toContain('starts it and stops the other music engine')
  expect(node.textContent).toContain('Create & queue')
  expect(node.querySelector('details')?.open).toBe(false)
  expect(node.querySelector('details summary')?.textContent).toContain('Advanced generators')
  expect(api.switchModel).not.toHaveBeenCalled()
})
it('keeps Music on the queue when a model is already active', async () => {
  const { node, router } = await mount('/music', 'yue2')
  expect(router.currentRoute.value.name).toBe('music')
  expect(modelButton(node, 'yue2').getAttribute('aria-pressed')).toBe('false')
  expect(node.textContent).toContain('Create & queue')
  expect(api.switchModel).not.toHaveBeenCalled()
})
it('switches the model through an explicit selection and preserves the Music selector', async () => {
  const { node, router } = await mount()
  const picker = node.querySelector('[aria-label="Music model"]')
  modelButton(node, 'yue2').click(); await settle()
  expect(router.currentRoute.value.fullPath).toBe('/music/yue2')
  expect(api.switchModel).toHaveBeenCalledExactlyOnceWith('yue2')
  expect(node.querySelector('[aria-label="Music model"]')).toBe(picker)
  expect(node.textContent).toContain('YuE generator')
  expect(modelButton(node, 'yue2').getAttribute('aria-pressed')).toBe('true')
})
it('does not switch a selected engine that is already running', async () => {
  const { node } = await mount('/music/ace-step', 'ace_step')
  modelButton(node, 'ace_step').click(); await settle()
  expect(api.switchModel).not.toHaveBeenCalled()
})
it('starts the selected stopped model and blocks duplicate requests until completion', async () => {
  let finish: ((status: OrchestratorStatus) => void) | undefined
  vi.mocked(api.switchModel).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node } = await mount()
  const button = modelButton(node, 'ace_step')
  button.click(); button.click(); await settle()
  expect(api.switchModel).toHaveBeenCalledExactlyOnceWith('ace_step')
  expect(button.disabled).toBe(true)
  expect(modelButton(node, 'yue2').disabled).toBe(true)
  finish?.(snapshot('ace_step')); await settle()
  expect(button.disabled).toBe(false)
})
it.each(['starting', 'stopping'] as const)('prevents selection while an engine is %s', async status => {
  const { node, store } = await mount()
  store._applySnapshot({ ...snapshot(), models: { ...snapshot().models, ace_step: { ...snapshot().models.ace_step, status } } }); await settle()
  expect(modelButton(node, 'ace_step').disabled).toBe(true)
  modelButton(node, 'yue2').click(); await settle()
  expect(api.switchModel).not.toHaveBeenCalled()
})
it('updates the selection on Back without switching engines again', async () => {
  const { node, router } = await mount()
  modelButton(node, 'yue2').click(); await settle(); router.back(); await settle()
  expect(modelButton(node, 'ace_step').getAttribute('aria-pressed')).toBe('true')
  expect(api.switchModel).toHaveBeenCalledTimes(1)
})
it('does not start a model when a guard cancels its navigation', async () => {
  const { node, router } = await mount()
  router.beforeEach(to => to.name === 'yue2' ? false : undefined)
  modelButton(node, 'yue2').click(); await settle()
  expect(router.currentRoute.value.name).toBe('ace-step')
  expect(api.switchModel).not.toHaveBeenCalled()
  expect(modelButton(node, 'ace_step').disabled).toBe(false)
})
it('does not start a model after teardown while its navigation is pending', async () => {
  const { node, router } = await mount()
  let finish: (() => void) | undefined
  router.beforeEach(to => to.name === 'yue2' ? new Promise<void>(resolve => { finish = resolve }) : undefined)
  modelButton(node, 'yue2').click(); await settle()
  app?.unmount(); app = undefined; finish?.(); await settle()
  expect(api.switchModel).not.toHaveBeenCalled()
})
it('does not start a model when another workspace supersedes pending navigation', async () => {
  const { node, router } = await mount()
  let finish: (() => void) | undefined
  router.beforeEach(to => to.name === 'yue2' ? new Promise<void>(resolve => { finish = resolve }) : undefined)
  modelButton(node, 'yue2').click(); await settle()
  await router.push('/settings'); finish?.(); await settle()
  expect(node.textContent).toBe('Settings')
  expect(api.switchModel).not.toHaveBeenCalled()
})
it('shows a safe error and enables retry when startup fails', async () => {
  vi.mocked(api.switchModel).mockRejectedValueOnce(new Error('/private/library/token-secret'))
  const { node } = await mount()
  modelButton(node, 'ace_step').click(); await settle()
  expect(node.querySelector('[role="alert"]')?.textContent).toContain('Music engine could not start')
  expect(node.textContent).not.toContain('token-secret')
  expect(modelButton(node, 'ace_step').disabled).toBe(false)
  modelButton(node, 'ace_step').click(); await settle()
  expect(api.switchModel).toHaveBeenCalledTimes(2)
})
it.each([['/ace-step', 'ace-step'], ['/yue2', 'yue2']])('preserves a legacy %s bookmark and query without starting a model', async (path, name) => {
  const { router } = await mount(`${path}?reference=retained#mix`)
  expect(router.currentRoute.value.name).toBe(name)
  expect(router.currentRoute.value.path).toBe(`/music${path}`)
  expect(router.currentRoute.value.query.reference).toBe('retained')
  expect(router.currentRoute.value.hash).toBe('#mix')
  expect(api.switchModel).not.toHaveBeenCalled()
})

it('opens cloud music without switching or stopping a running local model', async () => {
  const {node,router}=await mount('/music/ace-step','ace_step')
  const cloud=node.querySelector('a[data-model="openrouter"]')
  if (!(cloud instanceof HTMLAnchorElement)) throw new Error('Missing cloud selector')
  cloud.click(); await settle()
  expect(router.currentRoute.value.name).toBe('openrouter-music')
  expect(node.textContent).toContain('Cloud generator')
  expect(api.switchModel).not.toHaveBeenCalled(); expect(api.stopActive).not.toHaveBeenCalled()
})

it.each(['stable-audio3'])('opens %s without starting or stopping another engine', async slug => {
  const { node, router } = await mount('/music/ace-step', 'ace_step')
  const link = node.querySelector(`a[href="/music/${slug}"]`)
  if (!(link instanceof HTMLAnchorElement)) throw new Error(`Missing ${slug} selector`)
  link.click(); await settle()
  expect(router.currentRoute.value.path).toBe(`/music/${slug}`)
  expect(node.textContent).toContain(`${slug.replace('-', '_')} generator`)
  expect(api.switchModel).not.toHaveBeenCalled()
  expect(api.stopActive).not.toHaveBeenCalled()
})

it('offers only supported generation and training routes', async () => {
  const { node, router } = await mount('/music')
  expect([...node.querySelectorAll('a[data-model]')].map(link => link.getAttribute('data-model'))).toEqual(['stable_audio3', 'openrouter'])
  expect(router.hasRoute('heartmula')).toBe(false)
  expect(router.hasRoute('music-lora-heartmula')).toBe(false)
})
