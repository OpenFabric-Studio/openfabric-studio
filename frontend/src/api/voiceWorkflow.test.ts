// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import * as voices from './voices'
import { preparedVoice, comparison } from '../views/voice/voiceTestFixtures'

afterEach(() => vi.unstubAllGlobals())
function respond(value: unknown) {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(value)))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

it('sends source preparation options and validates the revision response', async () => {
  expect(voices.prepareVoice).toBeTypeOf('function')
  const fetcher = respond(preparedVoice())
  const body = { sources: [{ filename: 'song.wav', enabled: true, kind: 'song' }], separation_quality: 'high', clean: true, singer_confirmed: true } as const
  const controller = new AbortController()
  await expect(voices.prepareVoice('voice', { ...body, sources: [...body.sources] }, controller.signal)).resolves.toEqual(preparedVoice())
  expect(fetcher).toHaveBeenCalledWith('/api/voices/voice/prepare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal, body: JSON.stringify(body) })
})

it('sends reviewed selection with revision and cleaned IDs using PATCH', async () => {
  expect(voices.selectVoiceSamples).toBeTypeOf('function')
  const fetcher = respond(preparedVoice())
  const body = { revision: 'revision-1', segment_ids: ['segment-1'], reference_id: 'reference-1', cleaned_segment_ids: ['segment-1'] }
  await voices.selectVoiceSamples('voice', body)
  expect(fetcher).toHaveBeenCalledWith('/api/voices/voice/selection', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, signal: undefined, body: JSON.stringify(body) })
})

it('rejects invalid preparation measurements before rendering them', async () => {
  expect(voices.getVoicePreparation).toBeTypeOf('function')
  const response = preparedVoice()
  const segment = response.segments?.[0]
  if (!segment) throw new Error('Missing fixture segment')
  segment.score = 2
  respond(response)
  await expect(voices.getVoicePreparation('voice')).rejects.toThrow('Invalid VoicePreparationResponse')
})

it('validates comparison output measurements and rating request ranges', async () => {
  expect(voices.listVoiceComparisons).toBeTypeOf('function')
  expect(voices.rateVoiceTrial).toBeTypeOf('function')
  const response = comparison()
  const trial = response.trials[0]
  if (!trial?.metrics) throw new Error('Missing fixture trial')
  trial.metrics.peak = -1
  const fetcher = respond({ comparisons: [response] })
  await expect(voices.listVoiceComparisons('voice')).rejects.toThrow('Invalid VoiceComparisonsResponse')
  await expect(voices.rateVoiceTrial('voice', 'job', 'trial', { identity: 0, pitch: 3, intelligibility: 3, artifacts: 3 })).rejects.toThrow('Invalid VoiceTrialRating')
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('uploads held-out audio as a single file outside the training recordings route', async () => {
  expect(voices.uploadVoiceTrialSource).toBeTypeOf('function')
  const fetcher = respond({ id: 'abcdef1234567890abcdef1234567890', filename: 'held-out.wav', duration_sec: 20 })
  const file = new File(['audio'], 'held-out.wav', { type: 'audio/wav' })
  await voices.uploadVoiceTrialSource('voice', file)
  const options: unknown = fetcher.mock.calls[0]?.[1]
  if (typeof options !== 'object' || options === null || !('body' in options) || !(options.body instanceof FormData)) throw new Error('Expected multipart held-out upload')
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/voices/voice/trial-sources')
  expect(options.body.getAll('file')).toEqual([file])
})

it('changes audition URLs when a reviewed revision replaces audio under the same sample ID', () => {
  expect(voices.voiceSampleUrl('voice', 'segment', 'cleaned', 'revision 2')).toBe('/api/voices/voice/samples/segment?variant=cleaned&revision=revision%202')
  expect(voices.voiceReferenceUrl('voice', 'reference', 'revision 2')).toBe('/api/voices/voice/reference/reference?revision=revision%202')
})

it('validates duration bounds and sends a revision-owned coverage request', async () => {
  const fetcher = respond({ ...preparedVoice(), operation: 'coverage', status: 'queued' })
  const controller = new AbortController()
  await voices.analyzeVoiceCoverage('voice', 'revision-1', controller.signal)
  expect(fetcher).toHaveBeenCalledWith('/api/voices/voice/coverage', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal, body: JSON.stringify({ revision: 'revision-1' }) })
  await expect(voices.prepareVoice('voice', { max_selected_seconds: 3601 })).rejects.toThrow('Invalid PrepareVoiceRequest')
  await expect(voices.prepareVoice('voice', { max_selected_seconds: 59 })).rejects.toThrow('Invalid PrepareVoiceRequest')
  await expect(voices.analyzeVoiceCoverage('voice', '')).rejects.toThrow('Invalid AnalyzeVoiceCoverageRequest')
  expect(fetcher).toHaveBeenCalledTimes(1)
})
