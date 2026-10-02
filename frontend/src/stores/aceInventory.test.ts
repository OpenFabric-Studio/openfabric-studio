// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAceStepStore } from './aceStep'
import * as api from '../api/aceStep'

vi.mock('../api/aceStep', () => ({ modelInventory: vi.fn() }))
const inventory: api.ModelInventory = { models: [{ name: 'ace-step-turbo', is_default: true, is_loaded: true, supported_task_types: ['text2music'] }],
  default_model: 'ace-step-turbo', lm_models: [], loaded_lm_model: null, llm_initialized: false }
function deferred<T>() { let resolve: (value: T) => void = () => { throw new Error('Not initialized') }; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
beforeEach(() => { setActivePinia(createPinia()); vi.resetAllMocks() })
afterEach(() => { useAceStepStore().stopBackgroundTasks() })

it('exposes loading and failure while retaining the last successful inventory', async () => {
  const store = useAceStepStore()
  vi.mocked(api.modelInventory).mockResolvedValue(inventory)
  await store.loadInventory()
  expect(store.inventory).toEqual(inventory)
  const pending = deferred<api.ModelInventory>()
  vi.mocked(api.modelInventory).mockReturnValue(pending.promise)
  const loading = store.loadInventory()
  expect(store.inventoryLoading).toBe(true)
  expect(store.inventoryError).toBe('')
  pending.resolve(inventory); await loading
  vi.mocked(api.modelInventory).mockRejectedValueOnce(new Error('Malformed native response'))
  await store.loadInventory()
  expect(store.inventory).toEqual(inventory)
  expect(store.inventoryLoading).toBe(false)
  expect(store.inventoryError).toBe('inventory_unavailable')
  vi.mocked(api.modelInventory).mockResolvedValue(inventory)
  await store.loadInventory()
  expect(store.inventoryError).toBe('')
})

it('aborts overlapping requests and ignores a stale response even when fetch ignores abort', async () => {
  const older = deferred<api.ModelInventory>()
  vi.mocked(api.modelInventory).mockReturnValueOnce(older.promise).mockResolvedValueOnce(inventory)
  const store = useAceStepStore(); const first = store.loadInventory()
  const signal = vi.mocked(api.modelInventory).mock.calls[0]?.[0]
  await store.loadInventory()
  expect(signal?.aborted).toBe(true)
  older.resolve({ ...inventory, default_model: 'old', models: [] }); await first
  expect(store.inventory).toEqual(inventory)
  expect(store.inventoryLoading).toBe(false)
})

it('aborts inventory at teardown without overwriting prior data or displaying a stale error', async () => {
  const pending = deferred<api.ModelInventory>()
  vi.mocked(api.modelInventory).mockReturnValue(pending.promise)
  const store = useAceStepStore(); store.inventory = inventory
  const loading = store.loadInventory()
  const signal = vi.mocked(api.modelInventory).mock.calls[0]?.[0]
  store.stopBackgroundTasks()
  expect(signal?.aborted).toBe(true)
  pending.resolve({ ...inventory, models: [] }); await loading
  expect(store.inventory).toEqual(inventory)
  expect(store.inventoryLoading).toBe(false)
  expect(store.inventoryError).toBe('')
})
