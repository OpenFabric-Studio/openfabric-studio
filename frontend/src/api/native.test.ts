import { afterEach, expect, it, vi } from 'vitest'
import { health as aceHealth } from './aceStep'
import { generateTrack, parseTaskRunResult, health as yueHealth } from './yue2'
import { autoLabelStatus, updateSample } from './aceStepTraining'

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
