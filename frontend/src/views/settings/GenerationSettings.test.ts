// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import GenerationSettings from './GenerationSettings.vue'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/generationLibrary'
vi.mock('../../api/generationLibrary', async original => ({ ...await original<typeof import('../../api/generationLibrary')>(), getSettings: vi.fn(), updateSettings: vi.fn() }))
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); setLocale('en'); vi.mocked(api.getSettings).mockResolvedValue({ history_limit: 100, revision: 2 }); vi.mocked(api.updateSettings).mockResolvedValue({ history_limit: 1, revision: 3 }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let index = 0; index < 10; index++) await nextTick() }
async function mount() { const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerationSettings).use(i18n); app.mount(container); await settle(); return container }
it('explains metadata pruning and saves a bounded library-wide limit with its revision', async () => {
  const container = await mount(); expect(container.textContent).toContain('audio, active jobs and saved presets are retained')
  const input = container.querySelector('input[type=number]'); if (!(input instanceof HTMLInputElement)) throw new Error('Limit missing')
  input.value = '1'; input.dispatchEvent(new Event('input')); await settle(); container.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true })); await settle()
  expect(api.updateSettings).toHaveBeenCalledWith({ history_limit: 1, revision: 2 }, expect.any(AbortSignal))
})
it('refuses fractional and out-of-range retention values', async () => {
  const container = await mount(); const input = container.querySelector('input[type=number]'); if (!(input instanceof HTMLInputElement)) throw new Error('Limit missing')
  for (const value of ['0', '1.5', '10001']) { input.value = value; input.dispatchEvent(new Event('input')); await settle(); container.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true })); await settle() }
  expect(api.updateSettings).not.toHaveBeenCalled()
})
