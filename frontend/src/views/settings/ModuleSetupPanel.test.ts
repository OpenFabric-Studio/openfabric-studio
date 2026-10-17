// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/modules'
import { ApiError } from '../../api/http'
import * as terms from '../../api/musicTerms'
vi.mock('../../api/musicTerms', () => ({ getMusicTerms: vi.fn(), acceptMusicTerms: vi.fn(), updateMusicTermsSettings: vi.fn() }))
import { useModulesStore } from '../../stores/modules'
import ModuleSetupPanel from './ModuleSetupPanel.vue'
import type { ModuleInventory, ModulePlan, ModuleLicenseDeclaration, ModuleInstallJob } from '../../api/generated'

vi.mock('../../api/modules', () => ({ getModules: vi.fn(), listModuleJobs: vi.fn(), planModules: vi.fn(), installModules: vi.fn(), controlModuleJob: vi.fn() }))
const inventory: ModuleInventory = { platform: 'darwin', architecture: 'arm64', acceleration: 'apple_silicon', managed_root: '/managed', free_bytes: 100000000000, checked_at: '2026-10-03', modules: [
  { id: 'speech', name: 'GPT-SoVITS', description: 'Narration', state: 'partial', supported: true, managed: false, automation: 'manual', dependencies: ['media'], capabilities: [], evidence: [{ code: 'weights', detail: 'Weights need verification', verified: false }], actions: [{ kind: 'manual', label: 'Setup guide', detail: 'Install verified weights', url: 'https://github.com/RVC-Boss/GPT-SoVITS' }] },
  { id: 'video', name: 'LTX', description: 'Generated video', state: 'unsupported', supported: false, managed: false, automation: 'unsupported', dependencies: [], capabilities: [], evidence: [], actions: [] },
] }
const plan: ModulePlan = { features: ['speech'], download_models: true, plan_token: 'a'.repeat(64), steps: [{ module_id: 'speech', name: 'GPT-SoVITS', operation: 'manual', detail: 'Review the setup guide', actions: [] }], estimated_download_bytes: 0, download_size_unknown: true, required_free_bytes: 10, free_bytes: 100, can_install: true, warnings: ['Weights need manual verification'] }
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); setLocale('en'); vi.mocked(api.getModules).mockResolvedValue(inventory); vi.mocked(api.listModuleJobs).mockResolvedValue([]); vi.mocked(api.planModules).mockResolvedValue(plan) })
beforeEach(() => { vi.mocked(terms.getMusicTerms).mockImplementation(async (engine, operation) => ({ engine, operation, revision: 0, noncommercial_enabled: false, requirements: [], allowed: true })) })
afterEach(() => { app?.unmount(); document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount(hash = '') { const pinia = createPinia(); useModulesStore(pinia).inventory = inventory; const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings', component: { render: () => null } }] }); await router.push({ path: '/settings', hash }); app = createApp(ModuleSetupPanel).use(pinia).use(router).use(i18n); const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle(); return node }
function button(node: HTMLElement, label: string) { const found = [...node.querySelectorAll('button')].find(el => el.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
function modelChoice(node: HTMLElement) { const found = [...node.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find(input => !input.hasAttribute('value')); if (!found) throw new Error('Missing model download choice'); return found }

const stableModule: ModuleInventory['modules'][number] = { id: 'stable_audio3', name: 'Stable Audio 3', description: 'Instrumental music', state: 'missing', supported: true, managed: true, automation: 'automatic', dependencies: ['media'], capabilities: [], evidence: [], actions: [] }
const installedStable: ModuleInventory['modules'][number] = { ...stableModule, state: 'installed', can_reinstall: true }
async function openChoices(node: HTMLElement) { button(node, 'Automatic setup').click(); await settle(); button(node, 'Continue').click(); await settle() }
it('preselects backend starter choices and keeps customization closed until needed', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'] })
  const node = await mount(); await openChoices(node)
  expect(node.querySelector<HTMLInputElement>('input[value="speech"]')?.checked).toBe(true)
  expect(node.querySelector('input[value="speech"]')?.closest('details')?.open).toBe(false)
  expect(node.querySelector('[data-selected-features]')?.textContent).toContain('GPT-SoVITS')
  expect(button(node, 'Review setup').disabled).toBe(false)
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledWith({ features: ['speech'], download_models: true }, expect.any(AbortSignal))
  expect(api.installModules).not.toHaveBeenCalled()
})
it('includes required model files by default and resets an opt-out when opening fresh setup', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'] })
  const node = await mount()
  button(node, 'Refresh status').click(); await settle()
  expect(api.planModules).not.toHaveBeenCalled()
  expect(api.installModules).not.toHaveBeenCalled()
  await openChoices(node)
  expect(modelChoice(node).checked).toBe(true)
  expect(modelChoice(node).closest('details')?.open).toBe(false)
  expect(modelChoice(node).closest('label')?.textContent).toContain('Include required model files (recommended)')
  modelChoice(node).click(); await settle()
  expect(modelChoice(node).checked).toBe(false)
  button(node, 'Back').click(); await settle(); button(node, 'Continue').click(); await settle()
  expect(modelChoice(node).checked).toBe(false)
  button(node, 'Close setup').click(); await settle(); await openChoices(node)
  expect(modelChoice(node).checked).toBe(true)
  expect(api.planModules).not.toHaveBeenCalled()
  expect(api.installModules).not.toHaveBeenCalled()
})
it('reviews automatic model downloads, estimated size and terms before one explicit setup action', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['stable_audio3'], modules: [...inventory.modules, stableModule] })
  const reviewed: ModulePlan = { ...plan, features: ['stable_audio3'], estimated_download_bytes: 2500000000, steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3', operation: 'install', detail: 'Download missing model files and reuse verified files.', actions: [], licenses: [{ component_id: 'weights', name: 'Model files', scope: 'model', status: 'declared', declared_license: 'Model licence', notes: 'Read the model terms.', reviewed_at: '2026-10-15' }] }] }
  vi.mocked(api.planModules).mockResolvedValue(reviewed)
  vi.mocked(api.installModules).mockResolvedValue({ id: 'f'.repeat(32), state: 'queued', created_at: 'today', updated_at: 'today', features: ['stable_audio3'], download_models: true, steps: [] })
  const node = await mount(); await openChoices(node)
  expect(api.installModules).not.toHaveBeenCalled()
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledWith({ features: ['stable_audio3'], download_models: true }, expect.any(AbortSignal))
  expect(node.querySelector('[data-model-scope]')?.textContent).toContain('Required model files included')
  expect(node.textContent).toContain('2.5 GB')
  expect(node.querySelector('[data-license-review]')?.textContent).toContain('Read the model terms.')
  expect(api.installModules).not.toHaveBeenCalled()
  button(node, 'Start setup').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, plan_token: reviewed.plan_token }, expect.any(AbortSignal))
})
it('keeps an explicit model opt-out in the reviewed and submitted plan', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'] })
  vi.mocked(api.planModules).mockResolvedValue({ ...plan, download_models: false })
  vi.mocked(api.installModules).mockResolvedValue({ id: 'f'.repeat(32), state: 'queued', created_at: 'today', updated_at: 'today', features: ['speech'], download_models: false, steps: [] })
  const node = await mount(); await openChoices(node)
  modelChoice(node).click(); await settle()
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledWith({ features: ['speech'], download_models: false }, expect.any(AbortSignal))
  expect(node.querySelector('[data-model-scope]')?.textContent).toContain('Model files excluded')
  button(node, 'Start setup').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: ['speech'], download_models: false, plan_token: plan.plan_token }, expect.any(AbortSignal))
})
it('selects the feature in the Settings link instead of an unrelated starter engine', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, stableModule] })
  const node = await mount('#module-stable_audio3'); await openChoices(node)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="speech"]')?.checked).toBe(false)
  expect(node.querySelector('[data-selected-features]')?.textContent).toContain('Stable Audio 3')
  expect(node.textContent).not.toContain('Model files for Stable Audio 3 still need separate setup')
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledWith({ features: ['stable_audio3'], download_models: true }, expect.any(AbortSignal))
})
it('honors a per-feature setup button ahead of the Settings hash', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, stableModule] })
  const node = await mount('#module-speech')
  const details = node.querySelector('#module-stable_audio3 details')
  if (!(details instanceof HTMLDetailsElement)) throw new Error('Missing module details')
  details.open = true; details.querySelector('button')?.click(); await settle()
  button(node, 'Continue').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="speech"]')?.checked).toBe(false)
})
it('does not substitute another feature when the requested one is unavailable', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, installedStable] })
  const node = await mount('#module-video'); await openChoices(node)
  expect(node.querySelector<HTMLInputElement>('input[value="speech"]')?.checked).toBe(false)
  expect(button(node, 'Review setup').disabled).toBe(true)
  expect(node.querySelector('[data-automatic-unavailable]')?.textContent).toContain('LTX')
  expect(node.textContent).not.toContain('Existing installations are reused. Open Customize')
  expect(api.planModules).not.toHaveBeenCalled()
})
it('allows customization and resets to automatic choices without changing them on refresh', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, stableModule] })
  const node = await mount(); await openChoices(node)
  const custom = node.querySelector('input[value="speech"]')?.closest('details')
  if (!(custom instanceof HTMLDetailsElement)) throw new Error('Missing customization')
  custom.open = true
  node.querySelector<HTMLInputElement>('input[value="speech"]')?.click()
  node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.click(); await settle()
  useModulesStore().inventory = { ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, stableModule] }; await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(true)
  expect(node.querySelector('[data-selected-features]')?.textContent).toContain('Stable Audio 3')
  button(node, 'Use automatic choices').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="speech"]')?.checked).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(false)
})
it('prevents replacing the selected feature while its plan is being reviewed', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, stableModule] })
  let finish: ((value: ModulePlan) => void) | undefined
  vi.mocked(api.planModules).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const node = await mount(); await openChoices(node)
  button(node, 'Review setup').click(); await settle()
  try {
    expect(button(node, 'Automatic setup').disabled).toBe(true)
    const otherSetup = node.querySelector('#module-stable_audio3 details button')
    expect(otherSetup).toHaveProperty('disabled', true)
    if (otherSetup instanceof HTMLButtonElement) otherSetup.click()
    expect(node.querySelector<HTMLInputElement>('input[value="speech"]')?.checked).toBe(true)
  } finally { finish?.(plan); await settle() }
  expect(api.installModules).not.toHaveBeenCalled()
})
it('blocks review when refreshed inventory no longer supports a selected feature', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'] })
  const node = await mount(); await openChoices(node)
  useModulesStore().inventory = { ...inventory, modules: inventory.modules.map(module => ({ ...module, supported: false })) }; await settle()
  expect(button(node, 'Review setup').disabled).toBe(true)
  expect(node.textContent).toContain('A selected feature is no longer available')
  expect(api.planModules).not.toHaveBeenCalled()
})

it('locks installed and ready choices, labels every choice and shows existing installations without recommendations', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['stable_audio3'], modules: [...inventory.modules, installedStable, { ...stableModule, id: 'yue2', name: 'YuE', state: 'ready', managed: false }] })
  const node = await mount(); await openChoices(node)
  for (const id of ['stable_audio3', 'yue2']) {
    expect(node.querySelector<HTMLInputElement>(`input[value="${id}"]`)?.disabled).toBe(true)
    expect(node.querySelector<HTMLInputElement>(`input[value="${id}"]`)?.checked).toBe(false)
  }
  expect(node.querySelector('input[value="stable_audio3"]')?.closest('label')?.textContent).toContain('Installed')
  expect(node.querySelector('input[value="yue2"]')?.closest('label')?.textContent).toContain('Ready')
  expect(node.querySelector('input[value="speech"]')?.closest('label')?.textContent).toContain('Setup incomplete')
  expect(node.querySelector('input[value="video"]')?.closest('label')?.textContent).toContain('Unavailable on this computer')
  expect(node.querySelector('[data-installed-features]')?.textContent).toContain('Stable Audio 3')
  expect(node.querySelector('[data-installed-module="yue2"]')?.textContent).toContain('Dependency reinstallation is unavailable here')
  expect(node.querySelector('[data-installed-module="yue2"] button')).toBeNull()
  expect(button(node, 'Review setup').disabled).toBe(true)
  expect(node.textContent).toContain('No new setup selected')
  expect(node.textContent).toContain('Existing installations are reused. Open Customize to add features, or choose Reinstall dependencies where available.')
  expect(node.textContent).not.toContain('Continue with these choices')
  expect(api.planModules).not.toHaveBeenCalled()
})

it('filters installed recommendations while preserving selectable missing and incomplete features', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['stable_audio3', 'speech', 'kokoro'], modules: [...inventory.modules, installedStable, { ...stableModule, id: 'kokoro', name: 'Kokoro' }] })
  const node = await mount(); await openChoices(node)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(false)
  expect(node.querySelector<HTMLInputElement>('input[value="speech"]')?.checked).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="kokoro"]')?.checked).toBe(true)
  expect(node.querySelector('input[value="kokoro"]')?.closest('label')?.textContent).toContain('Not installed')
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['speech', 'kokoro'], download_models: true }, expect.any(AbortSignal))
})

it('requires explicit scoped dependency reinstallation and reviews preserved data before submission', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules: [...inventory.modules, installedStable] })
  const reviewed: ModulePlan = { ...plan, features: ['stable_audio3'], reinstall: ['stable_audio3'], steps: [{ module_id: 'media', name: 'Media tools', operation: 'reuse', detail: 'Existing tools will be reused.', actions: [] }, { module_id: 'stable_audio3', name: 'Stable Audio 3', operation: 'install', detail: 'Replace isolated packages.', actions: [] }] }
  vi.mocked(api.planModules).mockResolvedValue(reviewed)
  vi.mocked(api.installModules).mockResolvedValue({ ...manualJob, features: ['stable_audio3'], reinstall: ['stable_audio3'], state: 'queued' })
  const node = await mount('#module-stable_audio3'); await openChoices(node)
  const installed = node.querySelector<HTMLElement>('[data-installed-module="stable_audio3"]')
  if (!installed) throw new Error('Missing installed feature')
  button(installed, 'Reinstall dependencies').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.disabled).toBe(false)
  expect(installed.textContent).toContain('Cancel reinstallation')
  expect(node.textContent).toContain('replaces isolated packages')
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['stable_audio3'], download_models: true, reinstall: ['stable_audio3'] }, expect.any(AbortSignal))
  expect(node.querySelector('[data-reinstall-review]')?.textContent).toContain('Stable Audio 3')
  expect(node.querySelector('[data-reinstall-review]')?.textContent).toContain('models, datasets, checkpoints and configuration')
  expect(node.querySelector('[data-reused-module]')?.textContent).toContain('Installed')
  expect(api.installModules).not.toHaveBeenCalled()
  button(node, 'Start setup').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: reviewed.features, download_models: true, plan_token: reviewed.plan_token, reinstall: reviewed.reinstall }, expect.any(AbortSignal))
})

it('locks the ordinary installed module action and opens explicit dependency reinstallation separately', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules: [...inventory.modules, installedStable] })
  const node = await mount()
  const details = node.querySelector('#module-stable_audio3 details')
  if (!(details instanceof HTMLDetailsElement)) throw new Error('Missing module details')
  details.open = true
  expect(button(details, 'Installed').disabled).toBe(true)
  button(details, 'Reinstall dependencies').click(); await settle()
  button(node, 'Continue').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(true)
  expect(node.querySelector('[data-installed-module="stable_audio3"]')?.textContent).toContain('Cancel reinstallation')
  expect(api.planModules).not.toHaveBeenCalled()
})

it('uses only the reviewed reinstallation scope when the returned plan narrows the selection', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, installedStable] })
  vi.mocked(api.planModules).mockResolvedValue({ ...plan, reinstall: [] })
  vi.mocked(api.installModules).mockResolvedValue({ ...manualJob, state: 'queued' })
  const node = await mount(); await openChoices(node)
  const installed = node.querySelector<HTMLElement>('[data-installed-module="stable_audio3"]')
  if (!installed) throw new Error('Missing installed feature')
  button(installed, 'Reinstall dependencies').click(); await settle()
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['speech', 'stable_audio3'], download_models: true, reinstall: ['stable_audio3'] }, expect.any(AbortSignal))
  expect(node.querySelector('[data-reinstall-review]')).toBeNull()
  button(node, 'Start setup').click(); await settle()
  expect(api.installModules).toHaveBeenCalledExactlyOnceWith({ features: ['speech'], download_models: true, plan_token: plan.plan_token }, expect.any(AbortSignal))
})

it('cancels and resets reinstallation intent before ordinary setup', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'], modules: [...inventory.modules, installedStable] })
  const node = await mount(); await openChoices(node)
  const installed = node.querySelector<HTMLElement>('[data-installed-module="stable_audio3"]')
  if (!installed) throw new Error('Missing installed feature')
  button(installed, 'Reinstall dependencies').click(); await settle()
  button(installed, 'Cancel reinstallation').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.disabled).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(false)
  button(installed, 'Reinstall dependencies').click(); await settle()
  button(node, 'Use automatic choices').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.disabled).toBe(true)
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledExactlyOnceWith({ features: ['speech'], download_models: true }, expect.any(AbortSignal))
})

it('clears explicit reinstallation when its checkbox is unchecked or fresh setup opens', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules: [...inventory.modules, installedStable] })
  const node = await mount(); await openChoices(node)
  const installed = node.querySelector<HTMLElement>('[data-installed-module="stable_audio3"]')
  if (!installed) throw new Error('Missing installed feature')
  button(installed, 'Reinstall dependencies').click(); await settle()
  node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.disabled).toBe(true)
  expect(installed.textContent).not.toContain('Cancel reinstallation')
  button(installed, 'Reinstall dependencies').click(); await settle()
  button(node, 'Close setup').click(); await settle(); await openChoices(node)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.disabled).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.checked).toBe(false)
})

it('blocks a stale ordinary selection that becomes installed and invalidates revoked reinstallation capability', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['stable_audio3'], modules: [...inventory.modules, stableModule] })
  const node = await mount(); await openChoices(node)
  useModulesStore().inventory = { ...inventory, modules: [...inventory.modules, installedStable] }; await settle()
  expect(button(node, 'Review setup').disabled).toBe(true)
  expect(node.querySelector<HTMLInputElement>('input[value="stable_audio3"]')?.disabled).toBe(true)
  const installed = node.querySelector<HTMLElement>('[data-installed-module="stable_audio3"]')
  if (!installed) throw new Error('Missing installed feature')
  button(installed, 'Reinstall dependencies').click(); await settle()
  expect(button(node, 'Review setup').disabled).toBe(false)
  useModulesStore().inventory = { ...inventory, modules: [...inventory.modules, { ...installedStable, can_reinstall: false }] }; await settle()
  expect(button(node, 'Review setup').disabled).toBe(true)
  expect(api.planModules).not.toHaveBeenCalled()
})

it('blocks submission when an ordinary reviewed feature becomes installed', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'] })
  const node = await mount(); await openChoices(node); button(node, 'Review setup').click(); await settle()
  useModulesStore().inventory = { ...inventory, modules: inventory.modules.map(module => module.id === 'speech' ? { ...module, state: 'installed' } : module) }; await settle()
  expect(button(node, 'Start setup').disabled).toBe(true)
  button(node, 'Start setup').click(); await settle()
  expect(api.installModules).not.toHaveBeenCalled()
})

it.each([
  ['already_installed', 'already installed'],
  ['reinstall_unavailable', 'cannot reinstall dependencies'],
])('explains the %s setup error without exposing backend details', async (code, message) => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, recommended_features: ['speech'] })
  vi.mocked(api.planModules).mockRejectedValue(new ApiError(code, 409))
  const node = await mount(); await openChoices(node); button(node, 'Review setup').click(); await settle()
  expect(node.querySelector('[role="alert"]')?.textContent).toContain(message)
})

it('explains partial and unsupported modules with expandable evidence', async () => {
  const node = await mount(); expect(node.textContent).toContain('Setup incomplete'); expect(node.textContent).toContain('Unavailable on this computer')
  expect(node.querySelector('#module-speech details')?.textContent).toContain('Weights need verification')
  expect(node.querySelector('a[href="https://github.com/RVC-Boss/GPT-SoVITS"]')?.getAttribute('rel')).toContain('noopener')
})

it('requires feature selection and reviewed plan before submitting installation', async () => {
  const node = await mount(); button(node, 'Automatic setup').click(); await settle(); button(node, 'Continue').click(); await settle()
  const unsupported = node.querySelector<HTMLInputElement>('input[value="video"]'); expect(unsupported?.disabled).toBe(true)
  expect(button(node, 'Review setup').disabled).toBe(true)
  const speech = node.querySelector<HTMLInputElement>('input[value="speech"]'); if (!speech) throw new Error('Missing speech choice'); speech.click(); await settle()
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledWith({ features: ['speech'], download_models: true }, expect.any(AbortSignal))
  expect(node.textContent).toContain('lower bound'); expect(api.installModules).not.toHaveBeenCalled()
  vi.mocked(api.installModules).mockResolvedValue({ id: 'b'.repeat(32), state: 'awaiting_manual', created_at: 'today', updated_at: 'today', features: ['speech'], download_models: true, steps: [{ module_id: 'speech', name: 'GPT-SoVITS', state: 'manual', detail: 'Install verified weights' }] })
  button(node, 'Start setup').click(); await settle()
  expect(api.installModules).toHaveBeenCalledWith({ features: ['speech'], download_models: true, plan_token: plan.plan_token }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Manual steps needed')
  expect(document.activeElement?.id).toBe('setup-activity-title')
})

it('rejects unsafe action URLs and never displays raw request failures', async () => {
  const node = await mount(); useModulesStore().inventory = { ...inventory, modules: inventory.modules.map(m => ({ ...m, actions: [{ kind: 'manual', label: 'Unsafe', detail: 'Guide', url: 'javascript:alert(1)' }] })) }; await settle()
  expect(node.querySelector('a[href^="javascript:"]')).toBeNull()
  vi.mocked(api.planModules).mockRejectedValue(new Error('/private/secret traceback'))
  button(node, 'Automatic setup').click(); await settle(); button(node, 'Continue').click(); await settle(); node.querySelector<HTMLInputElement>('input[value="speech"]')?.click(); await settle(); button(node, 'Review setup').click(); await settle()
  expect(node.textContent).toContain('Could not prepare setup'); expect(node.textContent).not.toContain('/private/secret')
})

it('separates code and weight declarations, unknowns and source links before installation', async () => {
  const licenses: ModuleLicenseDeclaration[] = [
    { component_id: 'code', name: 'Integration code', scope: 'code', status: 'declared', declared_license: 'MIT', source_url: 'https://example.invalid/source/LICENSE', notes: 'Code terms only.', reviewed_at: '2026-10-06' },
    { component_id: 'weights', name: 'Local checkpoints', scope: 'model', status: 'unknown', notes: 'Local weight terms are not verified.', reviewed_at: '2026-10-06' },
  ]
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules: inventory.modules.map(module => ({ ...module, licenses })) })
  vi.mocked(api.planModules).mockResolvedValue({ ...plan, steps: plan.steps.map(item => ({ ...item, licenses })) })
  const node = await mount()
  const details = node.querySelector('#module-speech details')
  expect(details?.textContent).toContain('Code · MIT')
  expect(details?.textContent).toContain('Model weights · Terms unknown')
  expect(details?.textContent).toContain('Reviewed 2026-10-06')
  expect(details?.textContent).toContain('do not certify commercial permission')
  expect(details?.querySelector('a[href="https://example.invalid/source/LICENSE"]')?.getAttribute('rel')).toContain('noopener')
  button(node, 'Automatic setup').click(); await settle(); button(node, 'Continue').click(); await settle()
  node.querySelector<HTMLInputElement>('input[value="speech"]')?.click(); await settle(); button(node, 'Review setup').click(); await settle()
  expect(node.querySelector('[data-license-review]')?.textContent).toContain('Local weight terms are not verified.')
  expect(api.installModules).not.toHaveBeenCalled()
})

it('shows saved artifact bytes and retry attempts and the optional CPU setup stage', async () => {
  const node = await mount()
  const store = useModulesStore()
  store.inventory = { ...inventory, modules: [...inventory.modules, { id: 'speaker_review', name: 'Speaker review', description: 'Optional CPU screening', state: 'partial', supported: true, managed: true, automation: 'automatic', dependencies: [], capabilities: [], evidence: [{ code: 'weights', detail: 'Weights must be selected explicitly.', verified: false }], actions: [] }] }
  store.acceptJob({ id: 'c'.repeat(32), state: 'running', created_at: 'today', updated_at: 'today', current_step: 0, features: ['speech'], download_models: false, steps: [{ module_id: 'speech', name: 'Speech', state: 'running', detail: 'Downloading', download: { artifact_name: 'model.bin', bytes_received: 5, total_bytes: 20, attempt: 2, max_attempts: 3, phase: 'retrying', retry_after_sec: 1 } }] })
  await settle()
  expect(node.textContent).toContain('5 B / 20 B')
  expect(node.textContent).toContain('Attempt 2 of 3')
  expect(node.textContent).toContain('Retrying in 1 s')
  expect(node.querySelector('#module-speaker_review')?.textContent).toContain('Weights must be selected explicitly.')
})

it('shows the additional music module in Settings so generator setup links have targets', async () => {
  const modules: ModuleInventory['modules'] = (['stable_audio3'] as const).map(id => ({ id, name: id, description: 'Local music', state: 'partial', supported: true, managed: false, automation: 'manual', dependencies: [], capabilities: [], evidence: [], actions: [] }))
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules })
  const node = await mount()
  expect(node.querySelector('#module-heartmula')).toBeNull()
  expect(node.querySelector('#module-stable_audio3')).not.toBeNull()
})

const manualJob: ModuleInstallJob = {
  id: 'd'.repeat(32), state: 'awaiting_manual', created_at: '2026-10-15T15:50:20+00:00', updated_at: '2026-10-15T15:50:21+00:00', features: ['speech'], download_models: true,
  steps: [{ module_id: 'media', name: 'Media tools', state: 'verified', detail: 'Tool checks passed.' }, { module_id: 'speech', name: 'GPT-SoVITS', state: 'manual', detail: 'Packages checked. Model setup needs review.' }],
}
it('labels completed components honestly without creating manual setup steps for hardware quality', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules: [{ ...stableModule, state: 'installed' }] })
  vi.mocked(api.listModuleJobs).mockResolvedValue([{ ...manualJob, state: 'completed', features: ['stable_audio3'], steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3', state: 'verified', detail: 'Required components installed.' }] }])
  const node = await mount()
  const activity = node.querySelector('[aria-labelledby="setup-activity-title"]')
  expect(activity?.querySelector('[role="status"]')?.textContent).toBe('Setup complete')
  expect(activity?.textContent).toContain('Components are installed. Generation speed and output quality depend on your hardware and model.')
  expect(activity?.querySelector('[data-manual-instructions]')).toBeNull()
  expect(activity?.textContent).not.toContain('Manual steps needed')
  const module = node.querySelector('#module-stable_audio3')
  expect(module?.textContent).toContain('Installed')
  expect(module?.textContent).not.toContain('verify capability')
})
it('puts activity before the wizard and modules, with visible instructions for saved manual steps', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([manualJob])
  const node = await mount(); button(node, 'Automatic setup').click(); await settle()
  const activity = node.querySelector('[aria-labelledby="setup-activity-title"]')
  expect(activity).not.toBeNull()
  expect([...node.querySelectorAll('h3')].map(el => el.textContent?.trim()).slice(0, 2)).toEqual(['Setup activity', 'Computer check'])
  expect(activity?.textContent).toContain('1 verified · 1 manual · 2 total steps')
  expect(activity?.textContent).toContain('Started:')
  expect(activity?.textContent).toContain('Last job update:')
  expect(activity?.querySelectorAll('time[datetime]').length).toBe(2)
  expect(activity?.textContent).toContain('Model downloads requested')
  const instructions = activity?.querySelector('[data-manual-instructions]')
  expect(instructions?.closest('details')).toBeNull()
  expect(instructions?.textContent).toContain('What to do next')
  expect(instructions?.textContent).toContain('Install verified weights')
  expect(instructions?.textContent).toContain('Weights need verification')
  expect(instructions?.querySelector('a[href="https://github.com/RVC-Boss/GPT-SoVITS"]')?.getAttribute('rel')).toContain('noopener')
  expect(activity?.textContent).toContain('Resume setup rechecks')
  expect(api.controlModuleJob).not.toHaveBeenCalled()
  expect(api.installModules).not.toHaveBeenCalled()
})
it('keeps saved manual results while warning when current guidance cannot be refreshed', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([manualJob])
  vi.mocked(api.getModules).mockRejectedValue(new Error('private traceback'))
  const node = await mount()
  const instructions = node.querySelector('[data-manual-instructions]')
  expect(instructions?.textContent).toContain('Last known instructions')
  expect(instructions?.textContent).toContain('Install verified weights')
  useModulesStore().inventory = null; await settle()
  expect(instructions?.textContent).toContain('Refresh status to load instructions for this step')
  expect(node.textContent).not.toContain('private traceback')
})
it('offers a workspace link for manual music checks and rejects unsafe instruction URLs', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules: [{ ...stableModule, actions: [{ kind: 'verify', label: 'Verify preview', detail: 'Generate a short preview in Music.', url: null }, { kind: 'manual', label: 'Unsafe', detail: 'Bad guide', url: 'javascript:alert(1)' }] }] })
  vi.mocked(api.listModuleJobs).mockResolvedValue([{ ...manualJob, features: ['stable_audio3'], steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3', state: 'manual', detail: 'Review model files.' }] }])
  const node = await mount()
  const instructions = node.querySelector('[data-manual-instructions]')
  expect(instructions?.querySelector('a[href="/music/stable-audio3"]')?.textContent).toContain('Open Stable Audio 3')
  expect(instructions?.querySelector('a[href^="javascript:"]')).toBeNull()
})
it('resumes only the chosen saved job after an explicit click and keeps resume disabled while busy', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([manualJob])
  vi.mocked(api.controlModuleJob).mockResolvedValue({ ...manualJob, state: 'queued' })
  const node = await mount(); button(node, 'Resume setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenCalledWith(manualJob.id, 'resume', expect.any(AbortSignal))
  useModulesStore().acceptJob({ ...manualJob, id: 'e'.repeat(32) }); await settle()
  expect(button(node, 'Resume setup').disabled).toBe(true)
})

it('shows one current feature setup and compact collapsed history', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([
    { ...manualJob, id: 'f'.repeat(32), created_at: '2026-10-15T16:00:00Z' },
    { ...manualJob, id: 'e'.repeat(32), created_at: '2026-10-15T15:00:00Z', state: 'completed' },
    manualJob,
  ])
  const node = await mount()
  const activity = node.querySelector('[aria-labelledby="setup-activity-title"]')
  expect(activity?.querySelectorAll('[data-current-job]').length).toBe(1)
  expect(activity?.querySelector('[data-current-job] h4')?.textContent).toContain('GPT-SoVITS')
  const history = activity?.querySelector('[data-job-history]')
  expect(history).toBeInstanceOf(HTMLDetailsElement)
  expect(history).toHaveProperty('open', false)
  expect(history?.querySelector('summary')?.textContent).toContain('Previous setups (2)')
  expect(history?.querySelectorAll('[data-history-job]').length).toBe(2)
  expect(activity?.querySelector('[data-current-job]')?.closest('[data-job-history]')).toBeNull()
})

it('keeps an older active setup prominent ahead of a newer finished job', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([
    { ...manualJob, id: 'f'.repeat(32), state: 'completed', created_at: '2026-10-15T16:00:00Z' },
    { ...manualJob, id: 'e'.repeat(32), state: 'running', created_at: '2026-10-15T15:00:00Z' },
  ])
  const node = await mount()
  const current = node.querySelector('[data-current-job]')
  expect(current?.getAttribute('data-current-job')).toBe('e'.repeat(32))
  expect(current?.querySelector('[role="status"]')?.textContent).toBe('Installing')
  expect(current?.textContent).toContain('Cancel setup')
})

it('keeps a retried historical setup prominent after it completes and jobs reload', async () => {
  const newer: ModuleInstallJob = { ...manualJob, id: 'f'.repeat(32), state: 'failed', created_at: '2026-10-15T16:00:00Z', updated_at: '2026-10-15T18:00:00+02:00' }
  const older: ModuleInstallJob = { ...manualJob, id: 'e'.repeat(32), state: 'failed', created_at: '2026-10-15T15:00:00Z', updated_at: '2026-10-15T15:00:00Z' }
  vi.mocked(api.listModuleJobs).mockResolvedValue([newer, older])
  vi.mocked(api.controlModuleJob).mockResolvedValue({ ...older, state: 'queued' })
  const node = await mount()
  const history = node.querySelector('[data-job-history]')
  if (!(history instanceof HTMLDetailsElement)) throw new Error('Missing job history')
  history.open = true
  const attempt = history.querySelector('details')
  if (!(attempt instanceof HTMLDetailsElement)) throw new Error('Missing historical attempt')
  attempt.open = true; button(attempt, 'Retry setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenCalledExactlyOnceWith(older.id, 'resume', expect.any(AbortSignal))
  expect(node.querySelector('[data-current-job]')?.getAttribute('data-current-job')).toBe(older.id)
  vi.mocked(api.listModuleJobs).mockResolvedValue([newer, { ...older, state: 'completed', updated_at: '2026-10-15T17:30:00Z' }])
  await useModulesStore().refresh(); await settle()
  const current = node.querySelector('[data-current-job]')
  expect(current?.getAttribute('data-current-job')).toBe(older.id)
  expect(current?.querySelector('[role="status"]')?.textContent).toBe('Setup complete')
})

it('uses valid update timestamps and preserves server order for invalid or equal timestamps', async () => {
  const first: ModuleInstallJob = { ...manualJob, id: 'f'.repeat(32), updated_at: 'not a timestamp' }
  const second: ModuleInstallJob = { ...manualJob, id: 'e'.repeat(32), updated_at: '2026-10-15T17:00:00Z' }
  vi.mocked(api.listModuleJobs).mockResolvedValue([first, second])
  const node = await mount()
  expect(node.querySelector('[data-current-job]')?.getAttribute('data-current-job')).toBe(second.id)
  useModulesStore().jobs = [first, { ...second, updated_at: 'also invalid' }]; await settle()
  expect(node.querySelector('[data-current-job]')?.getAttribute('data-current-job')).toBe(first.id)
  useModulesStore().jobs = [{ ...first, updated_at: second.updated_at }, second]; await settle()
  expect(node.querySelector('[data-current-job]')?.getAttribute('data-current-job')).toBe(first.id)
})

it('announces failed setup with a failure count and retry instead of manual preview guidance', async () => {
  vi.mocked(api.getModules).mockResolvedValue({ ...inventory, modules: [{ ...stableModule, actions: [{ kind: 'verify', label: 'Verify preview', detail: 'Generate a preview.' }] }] })
  const failed: ModuleInstallJob = { ...manualJob, state: 'failed', error_code: 'module_install_failed', features: ['stable_audio3'], steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3', state: 'failed', detail: 'Converting model files', error_code: 'module_install_failed' }] }
  vi.mocked(api.listModuleJobs).mockResolvedValue([failed])
  vi.mocked(api.controlModuleJob).mockResolvedValue({ ...failed, state: 'queued' })
  const node = await mount()
  const current = node.querySelector('[data-current-job]')
  expect(current?.querySelector('[role="alert"]')?.textContent).toContain('Setup failed')
  expect(current?.querySelector('[role="alert"]')?.classList.contains('text-status-failed')).toBe(true)
  expect(current?.textContent).toContain('1 failed')
  expect(current?.textContent).toContain('Converting model files')
  expect(current?.querySelector('[data-manual-instructions]')).toBeNull()
  expect(current?.textContent).not.toContain('Verify preview')
  expect(current?.textContent).not.toContain('Resume setup rechecks')
  const technical = current?.querySelector('[data-job-technical]')
  expect(technical).toHaveProperty('open', false)
  expect(technical?.textContent).toContain('Technical details')
  expect(technical?.textContent).toContain(failed.id)
  expect(technical?.textContent).toContain('module_install_failed')
  expect(api.controlModuleJob).not.toHaveBeenCalled()
  button(node, 'Retry setup').click(); await settle()
  expect(api.controlModuleJob).toHaveBeenCalledExactlyOnceWith(failed.id, 'resume', expect.any(AbortSignal))
})

it('shows the structured failure, recovery and timestamped saved phase history', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([{ ...manualJob, state: 'failed', features: ['stable_audio3'], steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3', state: 'failed', detail: 'Preparing model files', last_output_at: '2026-10-15T15:50:20Z', failure: { code: 'installation_failed', summary: 'Model preparation could not finish.', recovery: 'Retry setup to reuse downloaded model files and repeat preparation.', phase: 'Preparing model files', retryable: true }, events: [{ at: '2026-10-15T15:50:00Z', detail: 'Checking downloaded model files' }, { at: '2026-10-15T15:50:10Z', detail: 'Preparing model files' }] }] }])
  const node = await mount()
  const current = node.querySelector('[data-current-job]')
  expect(current?.textContent).toContain('Model preparation could not finish.')
  expect(current?.textContent).toContain('Retry setup to reuse downloaded model files and repeat preparation.')
  expect(current?.querySelector('[data-last-output]')?.textContent).toContain('Last installer output:')
  expect(current?.querySelector('[data-last-output] time')?.getAttribute('datetime')).toBe('2026-10-15T15:50:20Z')
  const technical = current?.querySelector('[data-job-technical]')
  expect(technical?.textContent).toContain('installation_failed')
  expect(technical?.textContent).toContain('Checking downloaded model files')
  expect(technical?.querySelectorAll('[data-phase-event] time').length).toBe(2)
  expect(technical?.querySelector('[data-phase-event] time')?.getAttribute('datetime')).toBe('2026-10-15T15:50:00Z')
  expect(current?.querySelector('[role="progressbar"]')).toBeNull()
  expect(current?.textContent).not.toContain('%')
})

it('shows live phase and last output without a made-up percentage during conversion', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([{ ...manualJob, state: 'running', current_step: 0, features: ['stable_audio3'], steps: [{ module_id: 'stable_audio3', name: 'Stable Audio 3', state: 'running', detail: 'Preparing model files', last_output_at: '2026-10-15T15:50:20Z' }] }])
  const node = await mount()
  const current = node.querySelector('[data-current-job]')
  expect(current?.textContent).toContain('Preparing model files')
  expect(current?.querySelector('[data-last-output] time')?.textContent).not.toBe('—')
  expect(current?.querySelector('[role="progressbar"]')).toBeNull()
  expect(current?.textContent).not.toContain('%')
  expect(current?.querySelector('[data-phase-event]')).toBeNull()
  expect(current?.querySelector('[data-job-technical]')?.textContent).toContain('No saved phase history')
})

it('does not offer retry for a failure the backend marks unsafe to retry', async () => {
  vi.mocked(api.listModuleJobs).mockResolvedValue([{ ...manualJob, state: 'failed', steps: [{ module_id: 'speech', name: 'GPT-SoVITS', state: 'failed', detail: 'Checking installation path', failure: { code: 'unsafe_install_path', summary: 'The installation path needs attention.', recovery: 'Review the installation folder before starting a new setup.', retryable: false } }] }])
  const node = await mount()
  const current = node.querySelector('[data-current-job]')
  expect(current?.textContent).toContain('Review the installation folder before starting a new setup.')
  expect([...current?.querySelectorAll('button') ?? []].some(el => el.textContent === 'Retry setup')).toBe(false)
  expect(api.controlModuleJob).not.toHaveBeenCalled()
})
