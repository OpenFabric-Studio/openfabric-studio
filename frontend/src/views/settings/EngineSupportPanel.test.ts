// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import EngineSupportPanel from './EngineSupportPanel.vue'
import * as api from '../../api/support'
import { ApiError } from '../../api/http'
import type { EngineRuntimeResponse, SupportReport } from '../../api/generated'

vi.mock('../../api/support', async original => ({ ...await original<typeof import('../../api/support')>(), getEngineRuntime: vi.fn(), getSupportReport: vi.fn(), stopOwnedEngine: vi.fn(), downloadSupportReport: vi.fn() }))

let app: App | undefined
const runtime = (): EngineRuntimeResponse => ({ engines: [
  { id: 'ace_step', state: 'running', owned: true, instance_id: 'a'.repeat(32), idle_state: 'idle', can_stop: true },
  { id: 'yue2', state: 'stopped', owned: false, instance_id: null, idle_state: 'idle', can_stop: false },
] })
const report = (): SupportReport => ({ source: 'backend', app_version: '0.3.0-dev.0', python_version: '3.12.13', platform: 'darwin', architecture: 'arm64', acceleration: 'apple_silicon', physical_memory_bytes: null, backend_connection: 'reachable', modules: [], engines: [] })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getEngineRuntime).mockResolvedValue(runtime())
  vi.mocked(api.getSupportReport).mockResolvedValue(report())
  vi.mocked(api.stopOwnedEngine).mockResolvedValue({ engines: [] })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })
async function settle() { for (let index = 0; index < 8; index++) await nextTick() }
async function mount() {
  const container = document.body.appendChild(document.createElement('div'))
  app = createApp(EngineSupportPanel).use(i18n); app.mount(container); await settle(); return container
}
function button(container: HTMLElement, label: string) {
  const element = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label)
  if (!element) throw new Error(`Missing button ${label}`)
  return element
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>(success => { resolve = success })
  return { promise, resolve }
}

it('shows only owned persistent engine controls and explains external engines are managed separately', async () => {
  const container = await mount()
  expect(container.textContent).toContain('ACE-Step')
  expect(container.textContent).toContain('GPT-SoVITS')
  expect(button(container, 'Stop ACE-Step / free memory').disabled).toBe(false)
  expect(button(container, 'Stop YuE / free memory').disabled).toBe(true)
  button(container, 'Stop ACE-Step / free memory').click(); await settle()
  expect(api.stopOwnedEngine).toHaveBeenCalledWith({ engine_id: 'ace_step', instance_id: 'a'.repeat(32) }, expect.any(AbortSignal))
})

it('disables stopping when busy or unverified and explains why', async () => {
  const response = runtime(); const engine = response.engines[0]
  if (!engine) throw new Error('Missing fixture engine')
  engine.idle_state = 'unknown'; engine.can_stop = false
  vi.mocked(api.getEngineRuntime).mockResolvedValue(response)
  const container = await mount()
  expect(button(container, 'Stop ACE-Step / free memory').disabled).toBe(true)
  expect(container.textContent).toContain('Idle state could not be verified')
})

it('previews a report before allowing download and never automatically uploads', async () => {
  const container = await mount()
  expect(container.querySelector('pre')).toBeNull()
  expect(container.querySelector('a[download]')).toBeNull()
  expect(api.getSupportReport).not.toHaveBeenCalled()
  button(container, 'Preview support report').click(); await settle()
  expect(container.querySelector('pre')?.textContent).toContain('"platform": "darwin"')
  expect(button(container, 'Download reviewed JSON')).toBeDefined()
  expect(container.textContent).toContain('No automatic upload')
  const reviewed = container.querySelector('pre')?.textContent
  button(container, 'Download reviewed JSON').click(); await settle()
  expect(api.downloadSupportReport).toHaveBeenCalledWith(reviewed)
  expect(container.querySelector('pre')?.textContent).toBe(reviewed)
  expect(api.getSupportReport).toHaveBeenCalledOnce()
})

it('offers a bounded browser fallback if the backend is unreachable without exposing raw errors', async () => {
  vi.mocked(api.getEngineRuntime).mockRejectedValue(new TypeError('Failed to fetch /Users/private'))
  vi.mocked(api.getSupportReport).mockRejectedValue(new TypeError('Failed to fetch private api key'))
  const container = await mount(); button(container, 'Preview support report').click(); await settle()
  const json = container.querySelector('pre')?.textContent ?? ''
  expect(json).toContain('"source": "browser_fallback"')
  expect(json).toContain('"backend_connection": "unreachable"')
  expect(json).not.toContain('/Users')
  expect(container.textContent).not.toContain('api key')
  expect(container.textContent).toContain('Browser-only report')
})

it('reports a stale-run failure safely and refreshes instead of stopping a replacement', async () => {
  vi.mocked(api.stopOwnedEngine).mockRejectedValue(new ApiError('engine_changed', 409))
  const container = await mount(); button(container, 'Stop ACE-Step / free memory').click(); await settle()
  expect(container.textContent).toContain('The engine changed')
  expect(api.stopOwnedEngine).toHaveBeenCalledOnce()
  expect(api.getEngineRuntime).toHaveBeenCalledTimes(2)
})

it('aborts and ignores a late preview when the Settings panel is removed', async () => {
  const pending = deferred<SupportReport>(); vi.mocked(api.getSupportReport).mockReturnValue(pending.promise)
  const container = await mount(); button(container, 'Preview support report').click(); await settle()
  const signal = vi.mocked(api.getSupportReport).mock.calls[0]?.[0]
  expect(signal?.aborted).toBe(false)
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  pending.resolve(report()); await settle()
  expect(container.textContent).toBe('')
})

it('aborts an outstanding stop on teardown and ignores its late completion', async () => {
  const pending = deferred<EngineRuntimeResponse>(); vi.mocked(api.stopOwnedEngine).mockReturnValue(pending.promise)
  const container = await mount(); button(container, 'Stop ACE-Step / free memory').click(); await settle()
  const signal = vi.mocked(api.stopOwnedEngine).mock.calls[0]?.[1]
  expect(signal?.aborted).toBe(false)
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  pending.resolve({ engines: [] }); await settle()
  expect(container.textContent).toBe('')
  expect(api.getEngineRuntime).toHaveBeenCalledOnce()
})
