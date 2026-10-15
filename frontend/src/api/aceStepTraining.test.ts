import { afterEach, expect, it, vi } from 'vitest'
import { autoLabelStatus, checkTrainedLoraFiles, exportLora, latestAutoLabelStatus, latestPreprocessStatus } from './aceStepTraining'

afterEach(() => { vi.unstubAllGlobals() })
function response(data: unknown) { return new Response(JSON.stringify({ code: 200, data })) }
const idle = { task_id: null, status: 'idle', progress: '', current: 0, total: 0 }

it('decodes native idle latest endpoints and forwards abort ownership', async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(response(idle)))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  await expect(latestAutoLabelStatus(controller.signal)).resolves.toEqual(idle)
  await expect(latestPreprocessStatus(controller.signal)).resolves.toEqual(idle)
  expect(fetch).toHaveBeenNthCalledWith(1, '/api/ace/v1/dataset/auto_label_status', { signal: controller.signal })
  expect(fetch).toHaveBeenNthCalledWith(2, '/api/ace/v1/dataset/preprocess_status', { signal: controller.signal })
})

it('decodes latest live tasks while retaining the existing per-ID endpoint', async () => {
  const live = { task_id: 'accepted', status: 'running', progress: 'Labeling', current: 1, total: 4, save_path: '/dataset.json' }
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(response(live)))
  vi.stubGlobal('fetch', fetch)
  await expect(latestAutoLabelStatus()).resolves.toMatchObject(live)
  await expect(autoLabelStatus('accepted/task')).resolves.toMatchObject(live)
  expect(fetch).toHaveBeenLastCalledWith('/api/ace/v1/dataset/auto_label_status/accepted%2Ftask', { signal: undefined })
})

it.each([
  { ...idle, task_id: 17 }, { ...idle, current: '0' }, { ...idle, status: 'queued' },
  { task_id: null, status: 'running', progress: '', current: 0, total: 0 },
])('rejects malformed latest native task data', async data => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(data)))
  await expect(latestAutoLabelStatus()).rejects.toThrow('Invalid model response')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(data)))
  await expect(latestPreprocessStatus()).rejects.toThrow('Invalid model response')
})

it('rejects an active latest task without a usable task identifier', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(response({ task_id: '', status: 'running', progress: '', current: 0, total: 2 }))))
  await expect(latestAutoLabelStatus()).rejects.toThrow('Invalid model response')
  await expect(latestPreprocessStatus()).rejects.toThrow('Invalid model response')
})

it('exports through the app-owned endpoint and decodes the returned adapter path', async () => {
  const result = { message: 'Exported', export_path: '/exports/voice', adapter_path: '/exports/voice/adapter', source: '/engine/lora_output/run/final' }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(result)))
  vi.stubGlobal('fetch', fetch)
  const request = { export_path: '/exports/voice', lora_output_dir: 'lora_output/run' }
  await expect(exportLora(request)).resolves.toEqual(result)
  expect(fetch).toHaveBeenCalledWith('/api/lora-export', expect.objectContaining({ method: 'POST', body: JSON.stringify(request) }))
})

it('checks existing training files without an export destination and forwards cancellation', async () => {
  const result = { checkpoint: { source: '/engine/run/final', adapter_path: '/engine/run/final/adapter', kind: 'final' } }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(result)))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  await expect(checkTrainedLoraFiles({ lora_output_dir: 'run' }, controller.signal)).resolves.toEqual(result)
  expect(fetch).toHaveBeenCalledWith('/api/lora-export/check', expect.objectContaining({ method: 'POST', signal: controller.signal, body: JSON.stringify({ lora_output_dir: 'run' }) }))
})

it('accepts no saved adapter and rejects malformed checkpoint responses', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ checkpoint: null }))))
  await expect(checkTrainedLoraFiles({ lora_output_dir: 'run' })).resolves.toEqual({ checkpoint: null })
  for (const checkpoint of [{ source: '/run', adapter_path: '/run/adapter', kind: 'unknown' }, { source: 17, adapter_path: '/run/adapter', kind: 'final' }, { source: '/run', kind: 'checkpoint' }]) {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ checkpoint }))))
    await expect(checkTrainedLoraFiles({ lora_output_dir: 'run' })).rejects.toThrow()
  }
})
