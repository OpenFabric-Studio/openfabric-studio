import { afterEach, expect, it, vi } from 'vitest'
import { modelInventory } from './aceStep'

// Captured from the running native8001 and app9000 inventory endpoints.
// Source: acestep/api/http/model_service_routes.py::_collect_model_inventory.
const inventory = {
  models: [
    { name: 'acestep-v15-turbo', is_default: false, is_loaded: false, supported_task_types: ['text2music', 'repaint', 'cover', 'cover-nofsq'] },
    { name: 'acestep-v15-xl-base', is_default: false, is_loaded: false, supported_task_types: ['text2music', 'repaint', 'cover', 'cover-nofsq', 'extract', 'lego', 'complete'] },
    { name: 'acestep-v15-xl-sft', is_default: true, is_loaded: false, supported_task_types: ['text2music', 'repaint', 'cover', 'cover-nofsq', 'extract', 'lego', 'complete'] },
  ],
  default_model: 'acestep-v15-xl-sft',
  lm_models: [
    { name: 'acestep-5Hz-lm-1.7B', is_loaded: false },
    { name: 'acestep-5Hz-lm-4B', is_loaded: false },
  ],
  loaded_lm_model: null,
  llm_initialized: false,
}

function respond(payload: unknown) {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: payload, code: 200, error: null })))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

afterEach(() => vi.unstubAllGlobals())

it('keeps selectable DiT models when the real inventory contains LM objects', async () => {
  respond(inventory)
  const result = await modelInventory()
  expect(result.models).toEqual(inventory.models)
  expect(result.lm_models).toEqual(['acestep-5Hz-lm-1.7B', 'acestep-5Hz-lm-4B'])
  expect(result.default_model).toBe('acestep-v15-xl-sft')
  expect(result.models.every(model => !model.is_loaded)).toBe(true)
})

it('represents the native null default and empty inventory without inventing models', async () => {
  respond({ models: [], default_model: null, lm_models: [], loaded_lm_model: null, llm_initialized: false })
  await expect(modelInventory()).resolves.toEqual({ models: [], default_model: '', lm_models: [], loaded_lm_model: null, llm_initialized: false })
})

it.each([
  { entries: [{ name: 17, is_loaded: false }] },
  { entries: [{ name: 'acestep-5Hz-lm-4B', is_loaded: 'false' }] },
  { entries: [{ name: 'acestep-5Hz-lm-4B' }] },
  { entries: ['acestep-5Hz-lm-4B'] },
])('rejects malformed LM inventory entries $entries', async ({ entries }) => {
  respond({ ...inventory, lm_models: entries })
  await expect(modelInventory()).rejects.toThrow('Invalid model response')
})

it('forwards cancellation to the real inventory request', async () => {
  const fetch = respond(inventory)
  const signal = new AbortController().signal
  await modelInventory(signal)
  expect(fetch.mock.calls[0]?.[0]).toBe('/api/ace/v1/model_inventory')
  const init: unknown = fetch.mock.calls[0]?.[1]
  if (init === null || typeof init !== 'object' || !('signal' in init)) throw new Error('Missing inventory signal')
  expect(init.signal).toBe(signal)
})
