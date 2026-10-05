// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, ref, h, type App } from 'vue'
import { createPinia } from 'pinia'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/openrouter'
import ProvidersPanel from './ProvidersPanel.vue'
import type { OpenRouterStatus } from '../../api/generated'
vi.mock('../../api/openrouter', () => ({ getProviderStatus: vi.fn(), saveProviderSettings: vi.fn(), saveProviderKey: vi.fn(), removeProviderKey: vi.fn(), checkProviderConnection: vi.fn(), refreshProviderCatalog: vi.fn(), getProviderReceipts: vi.fn() }))
let app: App | undefined
const active = ref(true)
const status: OpenRouterStatus = { enabled: false, estimate_limit_usd: 1, credential_configured: false, credential_source: 'none', secure_storage_available: false }
async function settle() { for (let n = 0; n < 8; n++) await nextTick() }
async function mount() { const el = document.body.appendChild(document.createElement('div')); app = createApp({ render: () => h(ProvidersPanel, { active: active.value }) }).use(i18n).use(createPinia()); app.mount(el); await settle(); return el }
function button(el: HTMLElement, text: string) { const b = [...el.querySelectorAll('button')].find(b => b.textContent?.trim() === text); if (!b) throw new Error(`Missing ${text}`); return b }
function key(el: HTMLElement) { const input = el.querySelector('input[type=password]'); if (!(input instanceof HTMLInputElement)) throw new Error('Missing secret input'); return input }
beforeEach(() => { vi.clearAllMocks(); setLocale('en'); active.value = true; vi.mocked(api.getProviderStatus).mockResolvedValue({ ...status }); vi.mocked(api.saveProviderKey).mockResolvedValue({ ...status, credential_configured: true, credential_source: 'session' }); vi.mocked(api.getProviderReceipts).mockResolvedValue({ requests: [] }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('reads local status without contacting the remote catalog or connection', async () => { const el = await mount(); expect(api.getProviderStatus).toHaveBeenCalledOnce(); expect(api.checkProviderConnection).not.toHaveBeenCalled(); expect(api.refreshProviderCatalog).not.toHaveBeenCalled(); expect(el.textContent).toContain('Cloud providers'); expect(el.textContent).toContain('estimate'); expect(key(el).value).toBe('') })
it('sends a write-only session credential and clears it after saving', async () => { const el = await mount(); const field = key(el); field.value = 'sk-or-test-key-123456'; field.dispatchEvent(new Event('input', { bubbles: true })); await settle(); button(el, 'Save key').click(); await settle(); expect(api.saveProviderKey).toHaveBeenCalledWith({ api_key: 'sk-or-test-key-123456', persist: false }, expect.any(AbortSignal)); expect(key(el).value).toBe(''); expect(el.textContent).not.toContain('sk-or-test-key-123456') })
it('clears the secret and ignores late responses when hidden', async () => { let resolve: (s: OpenRouterStatus) => void = () => { throw new Error('not ready') }; vi.mocked(api.getProviderStatus).mockReturnValueOnce(new Promise(r => { resolve = r })); const el = await mount(); const field = key(el); field.value = 'sk-or-secret-123456'; field.dispatchEvent(new Event('input', { bubbles: true })); active.value = false; await settle(); expect(key(el).value).toBe(''); expect(vi.mocked(api.getProviderStatus).mock.calls[0]?.[0]?.aborted).toBe(true); resolve({ ...status, credential_configured: true }); await settle(); expect(el.textContent).not.toContain('Key configured') })
it('does not show internal errors or save secrets in browser storage', async () => { vi.mocked(api.getProviderStatus).mockRejectedValue(new Error('/secret/config')); const el = await mount(); expect(el.textContent).toContain('Could not complete'); expect(el.textContent).not.toContain('/secret/config'); expect(localStorage.getItem('openrouter_api_key')).toBeNull() })
