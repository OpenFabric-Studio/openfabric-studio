import { afterEach, expect, it, vi } from 'vitest'
import { health as aceHealth, loraLoad, loraUnload, loraToggle, loraScale } from './aceStep'
import { generateTrack, parseTaskRunResult, health as yueHealth } from './yue2'
import { autoLabelStatus, updateSample } from './aceStepTraining'
import { ApiError } from './http'

afterEach(() => { vi.unstubAllGlobals() })
const sample = { filename: 'song.wav', audio_path: 'song.wav', duration: 10, caption: 'folk', genre: '', prompt_override: null, lyrics: 'words', bpm: null, keyscale: '', timesignature: '', language: 'en', is_instrumental: false, labeled: true }

it('validates native response values instead of trusting JSON types', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 17, backend: 'cpp' }))))
  await expect(yueHealth()).rejects.toThrow('Invalid model response')
  expect(() => parseTaskRunResult({ audio: ['not-a-string'] })).toThrow('Invalid model response')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { status: 'ok', models_initialized: 'true' }, code: 200 }))))
  await expect(aceHealth()).rejects.toThrow('Invalid model response')
})

it('does not load or generate a model for an already-cancelled request', async () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  controller.abort()
  await expect(generateTrack('words', 1, { cot: 'off', style: 'folk' }, 'q8_0', controller.signal)).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('decodes the ACE sample update envelope and supplies the required sample index', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 200, data: { message: 'Updated', sample: { ...sample, index: 3 } } })))
  vi.stubGlobal('fetch', fetch)
  const result = await updateSample(3, { caption: 'folk' })
  expect(result.index).toBe(3)
  const init: unknown = fetch.mock.calls[0]?.[1]
  if (!init || typeof init !== 'object' || !('body' in init) || typeof init.body !== 'string') throw new Error('Missing request body')
  const body: unknown = JSON.parse(init.body)
  expect(body).toMatchObject({ sample_idx: 3, caption: 'folk' })
})

it('decodes auto-label samples whose index is carried separately by the native API', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 200, data: { task_id: 'task', status: 'running', progress: 'Labeling', current: 1, total: 2, last_updated_index: 3, last_updated_sample: sample } }))))
  const result = await autoLabelStatus('task')
  expect(result.last_updated_sample?.index).toBe(3)
})

it.each([
  { name: 'load', run: () => loraLoad('/adapter') },
  { name: 'unload', run: () => loraUnload() },
  { name: 'toggle', run: () => loraToggle(true) },
  { name: 'scale', run: () => loraScale(0.4) },
])('rejects native LoRA $name failure carried over HTTP 200 without exposing vendor errors', async ({ run }) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 400, data: null, error: '/private/model: vendor traceback' }))))
  await expect(run()).rejects.toMatchObject({ name: 'ApiError', message: 'native_operation_failed', status: 400 })
})

it('rejects a native failure before decoding apparently valid data', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 500, error: 'secret', data: { task_id: 'task', status: 'completed', progress: 'Done', current: 2, total: 2 } }))))
  await expect(autoLabelStatus('task')).rejects.toMatchObject({ name: 'ApiError', message: 'native_operation_failed', status: 500 })
})

it('accepts native code 200 success and rejects malformed LoRA acknowledgements', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 200, data: { message: 'Applied' } }))))
  await expect(loraScale(0.4)).resolves.toBeUndefined()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: '200', data: { message: 'Applied' } }))))
  await expect(loraScale(0.4)).rejects.toBeInstanceOf(ApiError)
})

it.each([-0.1, 1.5, 2, Infinity, NaN])('rejects unsupported ACE strength %s before sending a native request', async scale => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  await expect(loraScale(scale)).rejects.toMatchObject({ name: 'ApiError', message: 'native_lora_scale_invalid' })
  expect(fetch).not.toHaveBeenCalled()
})
