// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import * as api from '../../api/modules'
import { ApiError } from '../../api/http'
import { parseModuleInstallJob, parseModulePlan, type ModuleInstallJob, type ModulePlan } from '../../api/generated'
import MusicTrainingSetup from './MusicTrainingSetup.vue'
vi.mock('../../api/modules', () => ({ listModuleJobs: vi.fn(), planModules: vi.fn(), installModules: vi.fn(), controlModuleJob: vi.fn() }))
let app: App | undefined
const plan = () => parseModulePlan({ features: ['stable_audio3'], download_models: true, reinstall: [], stable_training_variant: 'small-music', plan_token: 'a'.repeat(64), steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3 training', operation: 'verify', estimated_download_bytes: 1133744146, detail: 'Download missing training files.', actions: [], licenses: [] }], estimated_download_bytes: 1133744146, download_size_unknown: false, required_free_bytes: 1500000000, free_bytes: 50000000000, can_install: true, warnings: [] })
const job = (state = 'running') => parseModuleInstallJob({ id: 'b'.repeat(32), state, created_at: '2026-10-15T10:00:00Z', updated_at: '2026-10-15T10:01:00Z', features: ['stable_audio3'], download_models: true, reinstall: [], stable_training_variant: 'small-music', steps: [{ module_id: 'stable_audio3', name: 'Training files', state: state === 'completed' ? 'verified' : 'running', detail: 'Checking downloaded training files.', error_code: null }], current_step: 0, error_code: null, restart_required: false })
const generationPlan = () => parseModulePlan({ ...plan(), stable_training_variant: null, stable_generation_variant: 'medium' })
const generationJob = (state = 'running') => parseModuleInstallJob({ ...job(state), id: 'c'.repeat(32), stable_training_variant: null, stable_generation_variant: 'medium', steps: [{ module_id: 'stable_audio3', name: 'Generation files', state: state === 'completed' ? 'verified' : 'running', detail: 'Checking generation files.', error_code: null }] })
async function settle() { for (let i = 0; i < 15; i++) await nextTick() }
async function mount(mode: 'training' | 'generation' | 'scores' = 'training', variant: 'small-music' | 'medium' = 'small-music', refresh = vi.fn(), engine: 'stable_audio3' | 'yue2' = 'stable_audio3') {
  const selection = ref({ mode, variant, engine })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: MusicTrainingSetup }, { path: '/settings', component: { render: () => null } }] })
  await router.push('/')
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(MusicTrainingSetup, { ...selection.value, supported: true, installed: true, ready: false, onRefresh: refresh }) }).use(createPinia()).use(router).use(i18n)
  app.mount(node); await settle(); return { node, selection }
}
function button(node: ParentNode, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.listModuleJobs).mockResolvedValue([]); vi.mocked(api.planModules).mockResolvedValue(plan()); vi.mocked(api.installModules).mockResolvedValue(job()); vi.mocked(api.controlModuleJob).mockResolvedValue(job('cancelled')) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
it('reviews a variant-specific download before preparing and never requests a reinstall', async () => {
  const { node } = await mount()
  expect(api.installModules).not.toHaveBeenCalled()
  button(node, 'Prepare training').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, stable_training_variant: 'small-music' }, expect.any(AbortSignal))
  expect(node.textContent).toContain('1.13 GB')
  button(node, 'Download and prepare').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, reinstall: [], stable_training_variant: 'small-music', plan_token: 'a'.repeat(64) }, expect.any(AbortSignal))
})
it('reattaches to preparation after reload and cancels the owned backend job only on request', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([job()])
  const { node } = await mount()
  expect(node.textContent).toContain('Checking downloaded training files')
  expect(api.planModules).not.toHaveBeenCalled()
  button(node, 'Cancel setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenCalledWith(job().id, 'cancel', expect.any(AbortSignal))
})
it('recovers an uncertain submission through history and prevents a duplicate prepare', async () => {
  vi.useFakeTimers()
  const { node } = await mount()
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
  const { node } = await mount()
  button(node, 'Prepare training').click(); await settle()
  expect(button(node, 'Download and prepare').disabled).toBe(true)
  button(node, 'Review again').click(); await settle()
  expect(api.planModules).toHaveBeenCalledTimes(2)
  expect(button(node, 'Download and prepare').disabled).toBe(false)
})
it('discards an expired approval token and lets the user review a fresh plan', async () => {
  vi.mocked(api.installModules).mockRejectedValueOnce(new ApiError('plan_changed', 409))
  const { node } = await mount()
  button(node, 'Prepare training').click(); await settle()
  button(node, 'Download and prepare').click(); await settle()
  expect(node.textContent).toContain('Setup changed')
  button(node, 'Prepare training').click(); await settle()
  expect(api.planModules).toHaveBeenCalledTimes(2)
})

it('reviews and starts only the selected generation download with the exact reviewed token', async () => {
  vi.mocked(api.planModules).mockResolvedValue(generationPlan())
  vi.mocked(api.installModules).mockResolvedValue(generationJob())
  const { node } = await mount('generation', 'medium')
  expect(api.planModules).not.toHaveBeenCalled()
  expect(api.installModules).not.toHaveBeenCalled()
  button(node, 'Prepare generation').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, stable_generation_variant: 'medium' }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Medium generation setup')
  button(node, 'Download and prepare').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, reinstall: [], stable_generation_variant: 'medium', plan_token: 'a'.repeat(64) }, expect.any(AbortSignal))
})

it('reattaches only to the selected generation job and preserves cancel and resume', async () => {
  const training = parseModuleInstallJob({ ...job(), stable_training_variant: 'medium' })
  vi.mocked(api.listModuleJobs).mockResolvedValueOnce([training, generationJob()]).mockResolvedValue([training, generationJob('cancelled')])
  vi.mocked(api.controlModuleJob).mockResolvedValueOnce(generationJob('cancelled')).mockResolvedValueOnce(generationJob())
  const { node } = await mount('generation', 'medium')
  expect(node.textContent).toContain('Checking generation files')
  expect(node.textContent).not.toContain('Checking downloaded training files')
  button(node, 'Cancel setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenNthCalledWith(1, generationJob().id, 'cancel', expect.any(AbortSignal))
  button(node, 'Resume setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenNthCalledWith(2, generationJob().id, 'resume', expect.any(AbortSignal))
})

it('refreshes readiness once when the selected generation job completes', async () => {
  vi.useFakeTimers()
  const refresh = vi.fn()
  vi.mocked(api.listModuleJobs).mockResolvedValueOnce([generationJob()]).mockResolvedValue([generationJob('completed')])
  const { node, selection } = await mount('generation', 'medium', refresh)
  expect(refresh).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(refresh).toHaveBeenCalledTimes(1)
  expect(node.textContent).toContain('Medium generation preparation')
  selection.value.variant = 'small-music'; await settle()
  selection.value.variant = 'medium'; await settle()
  expect(refresh).toHaveBeenCalledTimes(1)
})

it.each(['mode', 'variant'] as const)('aborts and drops a late generation review when %s changes', async field => {
  let finish: ((value: ModulePlan) => void) | undefined
  vi.mocked(api.planModules).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { node, selection } = await mount('generation', 'medium')
  button(node, 'Prepare generation').click(); await settle()
  const signal = vi.mocked(api.planModules).mock.calls[0]?.[1]
  if (field === 'mode') selection.value.mode = 'training'
  else selection.value.variant = 'small-music'
  await settle()
  expect(signal?.aborted).toBe(true)
  finish?.(generationPlan()); await settle()
  expect(node.textContent).not.toContain('Medium generation setup')
  expect(node.textContent).not.toContain('Download and prepare')
  expect(api.installModules).not.toHaveBeenCalled()
})

it.each(['mode', 'variant'] as const)('drops a late generation submission response when %s changes', async field => {
  let finish: ((value: ModuleInstallJob) => void) | undefined
  vi.mocked(api.planModules).mockResolvedValue(generationPlan())
  vi.mocked(api.installModules).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { node, selection } = await mount('generation', 'medium')
  button(node, 'Prepare generation').click(); await settle()
  button(node, 'Download and prepare').click(); await settle()
  const signal = vi.mocked(api.installModules).mock.calls[0]?.[1]
  if (field === 'mode') selection.value.mode = 'training'
  else selection.value.variant = 'small-music'
  await settle()
  expect(signal?.aborted).toBe(true)
  finish?.(generationJob()); await settle()
  expect(node.textContent).not.toContain('Checking generation files')
  expect(node.textContent).not.toContain('Download and prepare')
})

it('uses a generation-specific message when the reviewed plan expires', async () => {
  vi.mocked(api.planModules).mockResolvedValue(generationPlan())
  vi.mocked(api.installModules).mockRejectedValueOnce(new ApiError('plan_changed', 409))
  const { node } = await mount('generation', 'medium')
  button(node, 'Prepare generation').click(); await settle()
  button(node, 'Download and prepare').click(); await settle()
  expect(node.textContent).toContain('Review a fresh generation preparation plan')
  expect(button(node, 'Prepare generation').disabled).toBe(false)
})

const scorePlan = () => parseModulePlan({ ...plan(), features: ['yue2'], stable_training_variant: null, music_training_engine: null, steps: [{ module_id: 'yue2', name: 'YuE', operation: 'verify', estimated_download_bytes: 0, detail: 'Prepare automatic score extraction files.', actions: [], licenses: [] }], estimated_download_bytes: 0 })
const scoreJob = (state = 'running') => parseModuleInstallJob({ ...job(state), id: 'd'.repeat(32), features: ['yue2'], stable_training_variant: null, music_training_engine: null, steps: [{ module_id: 'yue2', name: 'YuE', state: state === 'completed' ? 'verified' : 'running', detail: 'Preparing automatic score extraction.', error_code: null }] })
it('prepares scores through the reviewed native YuE plan without another training installation', async () => {
  vi.mocked(api.planModules).mockResolvedValue(scorePlan())
  vi.mocked(api.installModules).mockResolvedValue(scoreJob())
  const { node } = await mount('scores', 'small-music', vi.fn(), 'yue2')
  button(node, 'Prepare automatic score extraction').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['yue2'], download_models: true }, expect.any(AbortSignal))
  expect(node.textContent).toContain('YuE automatic score extraction')
  button(node, 'Download and prepare').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: ['yue2'], download_models: true, reinstall: [], plan_token: 'a'.repeat(64) }, expect.any(AbortSignal))
})
it('reattaches to the ordinary native score job rather than a YuE training job', async () => {
  const training = parseModuleInstallJob({ ...scoreJob(), id: 'e'.repeat(32), music_training_engine: 'yue2', steps: [{ module_id: 'yue2', name: 'Trainer', state: 'running', detail: 'Preparing BF16 training files.' }] })
  vi.mocked(api.listModuleJobs).mockResolvedValue([scoreJob(), training])
  vi.mocked(api.controlModuleJob).mockResolvedValue(scoreJob('cancelled'))
  const { node } = await mount('scores', 'small-music', vi.fn(), 'yue2')
  expect(node.textContent).toContain('Preparing automatic score extraction')
  expect(node.textContent).not.toContain('Preparing BF16 training files')
  expect(button(node, 'Prepare automatic score extraction').disabled).toBe(true)
  button(node, 'Cancel setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenCalledExactlyOnceWith(scoreJob().id, 'cancel', expect.any(AbortSignal))
})
it('refreshes paired readiness when the native score job completes', async () => {
  vi.useFakeTimers()
  const refresh = vi.fn()
  vi.mocked(api.listModuleJobs).mockResolvedValueOnce([scoreJob()]).mockResolvedValue([scoreJob('completed')])
  await mount('scores', 'small-music', refresh, 'yue2')
  expect(refresh).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(refresh).toHaveBeenCalledTimes(1)
})
it('rejects a training plan returned to the score preparation action', async () => {
  vi.mocked(api.planModules).mockResolvedValue(parseModulePlan({ ...scorePlan(), music_training_engine: 'yue2' }))
  const { node } = await mount('scores', 'small-music', vi.fn(), 'yue2')
  button(node, 'Prepare automatic score extraction').click(); await settle()
  button(node, 'Download and prepare').click(); await settle()
  expect(api.installModules).not.toHaveBeenCalled()
})
