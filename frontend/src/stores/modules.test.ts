import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import * as api from '../api/modules'
import { useModulesStore } from './modules'
import type { ModuleInventory, ModuleInstallJob } from '../api/generated'

vi.mock('../api/modules', () => ({ getModules: vi.fn(), listModuleJobs: vi.fn() }))
const inventory: ModuleInventory = { platform: 'darwin', architecture: 'arm64', acceleration: 'apple_silicon', managed_root: '/managed', free_bytes: 100, checked_at: '2026-10-03', modules: [{ id: 'speech', name: 'Speech', description: 'Narration', state: 'ready', supported: true, managed: false, automation: 'manual', dependencies: [], capabilities: [], evidence: [], actions: [] }] }
beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks(); vi.useFakeTimers(); vi.mocked(api.getModules).mockResolvedValue(inventory); vi.mocked(api.listModuleJobs).mockResolvedValue([]) })
afterEach(() => { useModulesStore().stopPolling(); vi.clearAllTimers(); vi.useRealTimers() })

it('loses current-ready status on failed polling while retaining last evidence', async () => {
  const store = useModulesStore(); await store.refresh()
  expect(store.stateOf('speech')).toBe('ready')
  vi.mocked(api.getModules).mockRejectedValue(new Error('private traceback'))
  await store.refresh()
  expect(store.stateOf('speech')).toBe('unknown'); expect(store.inventory?.modules[0]?.name).toBe('Speech'); expect(store.offline).toBe(true)
})

it('rejects an older status response after a newer refresh', async () => {
  let complete: (value: ModuleInventory) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.getModules).mockReturnValueOnce(new Promise<ModuleInventory>(resolve => { complete = resolve }))
  const store = useModulesStore(); const old = store.refresh(); await store.refresh()
  complete({ ...inventory, modules: [] }); await old
  expect(store.inventory?.modules[0]?.id).toBe('speech')
})

it('ignores status returned after polling stops', async () => {
  let complete: (value: ModuleInventory) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.getModules).mockReturnValue(new Promise<ModuleInventory>(resolve => { complete = resolve }))
  const store = useModulesStore(); store.startPolling(); store.stopPolling(); complete(inventory)
  await Promise.resolve(); await Promise.resolve()
  expect(store.inventory).toBeNull()
})

it('retains a mutation result when an older job poll completes later', async () => {
  let complete: (value: ModuleInstallJob[]) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.listModuleJobs).mockReturnValueOnce(new Promise<ModuleInstallJob[]>(resolve => { complete = resolve }))
  const store = useModulesStore(); const old = store.refresh()
  store.acceptJob({ id: 'b'.repeat(32), state: 'queued', created_at: 'now', updated_at: 'now', features: ['speech'], download_models: false, steps: [] })
  complete([]); await old
  expect(store.busy).toBe(true); expect(store.jobs[0]?.state).toBe('queued')
})
