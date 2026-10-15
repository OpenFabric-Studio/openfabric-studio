import { afterEach, expect, it, vi } from 'vitest'
import { autoLabelStatus, latestAutoLabelStatus, latestPreprocessStatus } from './aceStepTraining'

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
