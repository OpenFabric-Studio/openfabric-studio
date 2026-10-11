// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import type { ClipHostStatus } from '../../api/contracts'
import { ApiError } from '../../api/http'
import * as api from '../../api/clipHost'
import ClipHostPanel from './ClipHostPanel.vue'
vi.mock('../../api/clipHost', () => ({ getClipHostStatus: vi.fn(), saveClipHostSettings: vi.fn(), checkClipHostConnection: vi.fn(), cleanupClipHostAssets: vi.fn() }))
let app: App | undefined
const active = ref(true)
const status: ClipHostStatus = { enabled: false, configured: false, binary_available: false, tunnel_active: false, active_clips: 0 }
const ready: ClipHostStatus = { ...status, enabled: true, configured: true, binary_available: true }
async function settle() { for (let n = 0; n < 8; n++) await nextTick() }
async function mount() { const el = document.body.appendChild(document.createElement('div')); app = createApp({ render: () => h(ClipHostPanel, { active: active.value }) }).use(i18n); app.mount(el); await settle(); return el }
function field(selector: string): HTMLInputElement { const value = document.querySelector(selector); if (!(value instanceof HTMLInputElement)) throw new Error(`Missing ${selector}`); return value }
function button(selector: string): HTMLButtonElement { const value = document.querySelector(selector); if (!(value instanceof HTMLButtonElement)) throw new Error(`Missing ${selector}`); return value }
beforeEach(() => { vi.resetAllMocks(); active.value = true; vi.mocked(api.getClipHostStatus).mockResolvedValue(status); vi.mocked(api.saveClipHostSettings).mockResolvedValue(ready); vi.mocked(api.checkClipHostConnection).mockResolvedValue(ready); vi.mocked(api.cleanupClipHostAssets).mockResolvedValue(ready) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('loads local setup without opening a tunnel and explains the clip-only relay', async () => {
  const el = await mount()
  expect(api.getClipHostStatus).toHaveBeenCalledOnce()
  expect(api.checkClipHostConnection).not.toHaveBeenCalled()
  expect(api.cleanupClipHostAssets).not.toHaveBeenCalled()
  expect(el.textContent).toContain('Cloudflare')
  expect(el.textContent).toContain('only the approved silent clip')
  expect(el.textContent).toContain('brew install cloudflared')
  expect(el.textContent).toContain('24 hours')
  expect(el.querySelector('[data-clip-host-docs]')?.getAttribute('href')).toBe('https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/')
  expect(el.querySelector('input[type=password]')).toBeNull()
  expect(el.querySelector('input[type=url]')).toBeNull()
  expect(el.textContent).not.toContain('Supabase')
})
it('saves only the local clip-sharing enable flag', async () => {
  await mount()
  const enabled = field('[data-clip-host-enabled]'); enabled.checked = true; enabled.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  button('[data-clip-host-save-settings]').click(); await settle()
  expect(api.saveClipHostSettings).toHaveBeenCalledExactlyOnceWith({ enabled: true }, expect.any(AbortSignal))
  expect(api.checkClipHostConnection).not.toHaveBeenCalled()
})
it('requires an explicit setup check and shows ready without an active tunnel', async () => {
  await mount(); button('[data-clip-host-check]').click(); await settle()
  expect(api.checkClipHostConnection).toHaveBeenCalledExactlyOnceWith(expect.any(AbortSignal))
  expect(document.body.textContent).toContain('Ready for approved video transfers')
  expect(document.body.textContent).toContain('No tunnel is open')
})
it('reports active links and explains that disabling only prevents new links', async () => {
  vi.mocked(api.getClipHostStatus).mockResolvedValue({ ...ready, tunnel_active: true, active_clips: 2 })
  vi.mocked(api.saveClipHostSettings).mockResolvedValue({ ...ready, enabled: false, configured: false, tunnel_active: true, active_clips: 2 })
  await mount()
  expect(document.body.textContent).toContain('Active clip links: 2')
  expect(document.body.textContent).toContain('prevents new links')
  const enabled = field('[data-clip-host-enabled]'); enabled.checked = false; enabled.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  button('[data-clip-host-save-settings]').click(); await settle()
  expect(api.saveClipHostSettings).toHaveBeenCalledWith({ enabled: false }, expect.any(AbortSignal))
  expect(document.body.textContent).toContain('Active clip links: 2')
})
it('cleans up expired grants only on an explicit request', async () => {
  vi.mocked(api.getClipHostStatus).mockResolvedValue({ ...ready, tunnel_active: true, active_clips: 1 })
  await mount(); button('[data-clip-host-cleanup]').click(); await settle()
  expect(api.cleanupClipHostAssets).toHaveBeenCalledExactlyOnceWith(expect.any(AbortSignal))
  expect(document.body.textContent).toContain('Expired links cleaned up')
})
it.each(['navigation', 'unmount'] as const)('aborts a setup check and discards its response after %s', async teardown => {
  let finish: (value: ClipHostStatus) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.checkClipHostConnection).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const el = await mount(); button('[data-clip-host-check]').click(); await settle()
  const signal = vi.mocked(api.checkClipHostConnection).mock.calls[0]?.[0]
  if (teardown === 'navigation') { active.value = false; await settle() }
  else { app?.unmount(); app = undefined }
  finish({ ...ready, tunnel_active: true }); await settle()
  expect(signal?.aborted).toBe(true)
  expect(el.textContent).not.toContain('Ready for approved video transfers')
})
it('keeps a later session when an older initial status load returns', async () => {
  let finish: (value: ClipHostStatus) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.getClipHostStatus).mockReturnValueOnce(new Promise(resolve => { finish = resolve })).mockResolvedValueOnce(ready)
  await mount(); active.value = false; await settle(); active.value = true; await settle()
  finish(status); await settle()
  expect(document.body.textContent).toContain('Ready for approved video transfers')
})
it('shows stable sharing errors without exposing backend details', async () => {
  vi.mocked(api.checkClipHostConnection).mockRejectedValue(new ApiError('clip_host_unavailable', 503))
  await mount(); button('[data-clip-host-check]').click(); await settle()
  expect(document.querySelector('[role=alert]')?.textContent).toContain('HTTPS connection')
  expect(document.querySelector('[role=alert]')?.textContent).not.toContain('Install')
  vi.mocked(api.checkClipHostConnection).mockRejectedValue(new Error('/private/config/token'))
  button('[data-clip-host-check]').click(); await settle()
  expect(document.querySelector('[role=alert]')?.textContent).toContain('Could not complete')
  expect(document.body.textContent).not.toContain('/private/config/token')
})
it.each([
  ['clip_host_binary_missing', 'Install cloudflared'],
  ['clip_host_unavailable', 'HTTPS connection could not fetch'],
  ['clip_host_tunnel_failed', 'could not start'],
  ['clip_host_edge_not_ready', 'not ready'],
  ['clip_host_delivery_invalid', 'No paid submission was sent'],
  ['clip_host_storage_unavailable', 'local clip-sharing metadata'],
  ['clip_host_worker_unresolved', 'ownership could not be verified'],
  ['clip_host_stopping', 'shutting down'],
])('explains the confirmed sharing error %s even when the binary is installed', async (code, message) => {
  vi.mocked(api.getClipHostStatus).mockResolvedValue(ready)
  vi.mocked(api.checkClipHostConnection).mockRejectedValue(new ApiError(code, 503))
  await mount(); button('[data-clip-host-check]').click(); await settle()
  expect(document.querySelector('[role=alert]')?.textContent).toContain(message)
  if (code !== 'clip_host_binary_missing') expect(document.querySelector('[role=alert]')?.textContent).not.toContain('Install cloudflared')
})
