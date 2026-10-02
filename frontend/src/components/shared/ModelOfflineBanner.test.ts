// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import ModelOfflineBanner from './ModelOfflineBanner.vue'
import { useOrchestratorStore } from '../../stores/orchestrator'
import * as api from '../../api/orchestrator'
import { i18n, setLocale } from '../../i18n'
import type { ModelId, ModelRuntimeStatus, OrchestratorStatus } from '../../types'

vi.mock('../../api/orchestrator', () => ({ getStatus: vi.fn(), switchModel: vi.fn(), stopActive: vi.fn() }))
let app: App | undefined
const snapshot: OrchestratorStatus = { active_model: null, models: {
  ace_step: { id: 'ace_step', label: 'ACE-Step 1.5', status: 'stopped', error: null },
  yue2: { id: 'yue2', label: 'YuE2-3B', status: 'stopped', error: null },
} }
beforeEach(() => { vi.clearAllMocks(); setLocale('en'); vi.mocked(api.getStatus).mockResolvedValue(snapshot); vi.mocked(api.switchModel).mockResolvedValue(snapshot) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick(); await new Promise<void>(resolve => setTimeout(resolve, 0)); await nextTick() }
async function mount(id: ModelId = 'ace_step', status: ModelRuntimeStatus = 'stopped', error: string | null = null) {
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/music/ace-step', name: 'ace-step', component: { render: () => null } },
    { path: '/music/yue2', name: 'yue2', component: { render: () => null } },
  ] })
  await router.push('/music/ace-step')
  const visible = ref(true)
  app = createApp({ render: () => visible.value ? h(ModelOfflineBanner, { modelId: id, status, error }) : null }).use(pinia).use(router).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle()
  const start = node.querySelector('button'); if (!start) throw new Error('Missing start action')
  return { node, start, router, store: useOrchestratorStore(pinia), hide: () => { visible.value = false } }
}

it('prevents duplicate starts from the offline banner', async () => {
  let finish: ((value: OrchestratorStatus) => void) | undefined
  vi.mocked(api.switchModel).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { start } = await mount()
  start.click(); start.click(); await settle()
  expect(api.switchModel).toHaveBeenCalledTimes(1)
  expect(start.disabled).toBe(true)
  finish?.(snapshot); await settle(); expect(start.disabled).toBe(false)
})
it('does not queue another start while the orchestrator is switching', async () => {
  const { start, store } = await mount()
  store.switching = true; await settle(); expect(start.disabled).toBe(true)
  start.click(); await settle(); expect(api.switchModel).not.toHaveBeenCalled()
})
it('does not start an engine after its banner unmounts during navigation', async () => {
  const { start, router, hide } = await mount('yue2')
  let finish: (() => void) | undefined
  router.beforeEach(to => to.name === 'yue2' ? new Promise<void>(resolve => { finish = resolve }) : undefined)
  start.click(); await settle(); hide(); await settle()
  finish?.(); await settle(); expect(api.switchModel).not.toHaveBeenCalled()
})
it('uses a safe explanation instead of displaying internal engine errors', async () => {
  const { node } = await mount('ace_step', 'error', '/private/library/token-secret traceback')
  expect(node.textContent).not.toContain('token-secret')
  expect(node.textContent).toContain('Music engine could not start')
})
