// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/modules'
import { useModulesStore } from '../../stores/modules'
import ModuleSetupPanel from './ModuleSetupPanel.vue'
import type { ModuleInventory, ModulePlan } from '../../api/generated'

vi.mock('../../api/modules', () => ({ getModules: vi.fn(), listModuleJobs: vi.fn(), planModules: vi.fn(), installModules: vi.fn(), controlModuleJob: vi.fn() }))
const inventory: ModuleInventory = { platform: 'darwin', architecture: 'arm64', acceleration: 'apple_silicon', managed_root: '/managed', free_bytes: 100000000000, checked_at: '2026-10-03', modules: [
  { id: 'speech', name: 'GPT-SoVITS', description: 'Narration', state: 'partial', supported: true, managed: false, automation: 'manual', dependencies: ['media'], capabilities: [], evidence: [{ code: 'weights', detail: 'Weights need verification', verified: false }], actions: [{ kind: 'manual', label: 'Setup guide', detail: 'Install verified weights', url: 'https://github.com/RVC-Boss/GPT-SoVITS' }] },
  { id: 'video', name: 'LTX', description: 'Generated video', state: 'unsupported', supported: false, managed: false, automation: 'unsupported', dependencies: [], capabilities: [], evidence: [], actions: [] },
] }
const plan: ModulePlan = { features: ['speech'], download_models: false, plan_token: 'a'.repeat(64), steps: [{ module_id: 'speech', name: 'GPT-SoVITS', operation: 'manual', detail: 'Review the setup guide', actions: [] }], estimated_download_bytes: 0, download_size_unknown: true, required_free_bytes: 10, free_bytes: 100, can_install: true, warnings: ['Weights need manual verification'] }
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); setLocale('en'); vi.mocked(api.getModules).mockResolvedValue(inventory); vi.mocked(api.listModuleJobs).mockResolvedValue([]); vi.mocked(api.planModules).mockResolvedValue(plan) })
afterEach(() => { app?.unmount(); document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount() { const pinia = createPinia(); useModulesStore(pinia).inventory = inventory; app = createApp(ModuleSetupPanel).use(pinia).use(i18n); const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle(); return node }
function button(node: HTMLElement, label: string) { const found = [...node.querySelectorAll('button')].find(el => el.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }

it('explains partial and unsupported modules with expandable evidence', async () => {
  const node = await mount(); expect(node.textContent).toContain('Setup incomplete'); expect(node.textContent).toContain('Unavailable on this computer')
  expect(node.querySelector('#module-speech details')?.textContent).toContain('Weights need verification')
  expect(node.querySelector('a[href="https://github.com/RVC-Boss/GPT-SoVITS"]')?.getAttribute('rel')).toContain('noopener')
})

it('requires feature selection and reviewed plan before submitting installation', async () => {
  const node = await mount(); button(node, 'Set up features').click(); await settle(); button(node, 'Continue').click(); await settle()
  const unsupported = node.querySelector<HTMLInputElement>('input[value="video"]'); expect(unsupported?.disabled).toBe(true)
  expect(button(node, 'Review setup').disabled).toBe(true)
  const speech = node.querySelector<HTMLInputElement>('input[value="speech"]'); if (!speech) throw new Error('Missing speech choice'); speech.click(); await settle()
  button(node, 'Review setup').click(); await settle()
  expect(api.planModules).toHaveBeenCalledWith({ features: ['speech'], download_models: false }, expect.any(AbortSignal))
  expect(node.textContent).toContain('lower bound'); expect(api.installModules).not.toHaveBeenCalled()
  vi.mocked(api.installModules).mockResolvedValue({ id: 'b'.repeat(32), state: 'awaiting_manual', created_at: 'today', updated_at: 'today', features: ['speech'], download_models: false, steps: [{ module_id: 'speech', name: 'GPT-SoVITS', state: 'manual', detail: 'Install verified weights' }] })
  button(node, 'Start setup').click(); await settle()
  expect(api.installModules).toHaveBeenCalledWith({ features: ['speech'], download_models: false, plan_token: plan.plan_token }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Manual steps needed')
})

it('rejects unsafe action URLs and never displays raw request failures', async () => {
  const node = await mount(); useModulesStore().inventory = { ...inventory, modules: inventory.modules.map(m => ({ ...m, actions: [{ kind: 'manual', label: 'Unsafe', detail: 'Guide', url: 'javascript:alert(1)' }] })) }; await settle()
  expect(node.querySelector('a[href^="javascript:"]')).toBeNull()
  vi.mocked(api.planModules).mockRejectedValue(new Error('/private/secret traceback'))
  button(node, 'Set up features').click(); await settle(); button(node, 'Continue').click(); await settle(); node.querySelector<HTMLInputElement>('input[value="speech"]')?.click(); await settle(); button(node, 'Review setup').click(); await settle()
  expect(node.textContent).toContain('Could not prepare setup'); expect(node.textContent).not.toContain('/private/secret')
})
