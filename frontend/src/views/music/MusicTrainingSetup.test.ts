// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import * as api from '../../api/modules'
import { ApiError } from '../../api/http'
import { parseModuleInstallJob, parseModulePlan } from '../../api/generated'
import MusicTrainingSetup from './MusicTrainingSetup.vue'
vi.mock('../../api/modules', () => ({ listModuleJobs: vi.fn(), planModules: vi.fn(), installModules: vi.fn(), controlModuleJob: vi.fn() }))
let app: App | undefined
const plan = () => parseModulePlan({ features: ['stable_audio3'], download_models: true, reinstall: [], stable_training_variant: 'small-music', plan_token: 'a'.repeat(64), steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3 training', operation: 'verify', estimated_download_bytes: 1133744146, detail: 'Download missing training files.', actions: [], licenses: [] }], estimated_download_bytes: 1133744146, download_size_unknown: false, required_free_bytes: 1500000000, free_bytes: 50000000000, can_install: true, warnings: [] })
const job = (state = 'running') => parseModuleInstallJob({ id: 'b'.repeat(32), state, created_at: '2026-10-15T10:00:00Z', updated_at: '2026-10-15T10:01:00Z', features: ['stable_audio3'], download_models: true, reinstall: [], stable_training_variant: 'small-music', steps: [{ module_id: 'stable_audio3', name: 'Training files', state: state === 'completed' ? 'verified' : 'running', detail: 'Checking downloaded training files.', error_code: null }], current_step: 0, error_code: null, restart_required: false })
async function settle() { for (let i = 0; i < 15; i++) await nextTick() }
async function mount() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: MusicTrainingSetup }, { path: '/settings', component: { render: () => null } }] })
  await router.push('/')
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp(MusicTrainingSetup, { variant: 'small-music', supported: true, installed: true, ready: false }).use(createPinia()).use(router).use(i18n)
  app.mount(node); await settle(); return node
}
function button(node: ParentNode, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.listModuleJobs).mockResolvedValue([]); vi.mocked(api.planModules).mockResolvedValue(plan()); vi.mocked(api.installModules).mockResolvedValue(job()); vi.mocked(api.controlModuleJob).mockResolvedValue(job('cancelled')) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
it('reviews a variant-specific download before preparing and never requests a reinstall', async () => {
  const node = await mount()
  expect(api.installModules).not.toHaveBeenCalled()
  button(node, 'Prepare training').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, stable_training_variant: 'small-music' }, expect.any(AbortSignal))
  expect(node.textContent).toContain('1.13 GB')
  button(node, 'Download and prepare').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, reinstall: [], stable_training_variant: 'small-music', plan_token: 'a'.repeat(64) }, expect.any(AbortSignal))
})
it('reattaches to preparation after reload and cancels the owned backend job only on request', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([job()])
  const node = await mount()
  expect(node.textContent).toContain('Checking downloaded training files')
  expect(api.planModules).not.toHaveBeenCalled()
  button(node, 'Cancel setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenCalledWith(job().id, 'cancel', expect.any(AbortSignal))
})
it('recovers an uncertain submission through history and prevents a duplicate prepare', async () => {
  vi.useFakeTimers()
  const node = await mount()
  button(node, 'Prepare training').click(); await settle()
  vi.mocked(api.installModules).mockRejectedValueOnce(new Error('/private/token'))
  vi.mocked(api.listModuleJobs).mockResolvedValueOnce([job()])
  button(node, 'Download and prepare').click(); await settle()
  expect(node.textContent).not.toContain('/private/token')
  expect(api.installModules).toHaveBeenCalledTimes(1)
  expect(node.textContent).toContain('Checking downloaded training files')
})
it('aborts polling on teardown without stopping preparation', async () => {
  vi.useFakeTimers()
  vi.mocked(api.listModuleJobs).mockResolvedValueOnce([job()]).mockReturnValueOnce(new Promise(() => {}))
  await mount(); await vi.advanceTimersByTimeAsync(2500)
  const signal = vi.mocked(api.listModuleJobs).mock.calls.at(-1)?.[0]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  expect(api.controlModuleJob).not.toHaveBeenCalled()
})

it('can review a blocked plan again after disk conditions improve', async () => {
  vi.mocked(api.planModules).mockResolvedValueOnce({ ...plan(), can_install: false, warnings: ['Not enough disk space'] }).mockResolvedValueOnce(plan())
  const node = await mount()
  button(node, 'Prepare training').click(); await settle()
  expect(button(node, 'Download and prepare').disabled).toBe(true)
  button(node, 'Review again').click(); await settle()
  expect(api.planModules).toHaveBeenCalledTimes(2)
  expect(button(node, 'Download and prepare').disabled).toBe(false)
})
it('discards an expired approval token and lets the user review a fresh plan', async () => {
  vi.mocked(api.installModules).mockRejectedValueOnce(new ApiError('plan_changed', 409))
  const node = await mount()
  button(node, 'Prepare training').click(); await settle()
  button(node, 'Download and prepare').click(); await settle()
  expect(node.textContent).toContain('Setup changed')
  button(node, 'Prepare training').click(); await settle()
  expect(api.planModules).toHaveBeenCalledTimes(2)
})
