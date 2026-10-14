// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import * as api from './recordingSessions'
import type { RecordingSession } from './contracts'

const voiceId = 'a'.repeat(32)
const session: RecordingSession = { id: 'b'.repeat(32), voice_id: voiceId, purpose: 'training', labels: { language: 'Spanish', register: 'chest', articulation: 'clear', expression: 'gentle' },
  original_filename: 'take.wav', relative_path: 'recordings/take.wav', original_sha256: 'c'.repeat(64), file_bytes: 100, created_at: 'now', format: 'WAV', sample_rate: 48000, channels: 1, subtype: 'PCM_24', duration_sec: 10,
  qc: { peak: .8, clipped_fraction: 0, rms_dbfs: -20, near_silence_fraction: .1, noise_floor_proxy_dbfs: -60, samples_analyzed: 480000, warnings: ['noise_estimate_is_amplitude_proxy'] } }
afterEach(() => vi.unstubAllGlobals())
it('loads owned schema-validated sessions and forwards cancellation', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ voice_id: voiceId, sessions: [session] })))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.listRecordingSessions(voiceId, controller.signal)).toEqual([session])
  expect(fetch).toHaveBeenCalledWith(`/api/voices/${voiceId}/recording-sessions`, { signal: controller.signal })
})
it('rejects foreign ownership and impossible measurements', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ voice_id: voiceId, sessions: [{ ...session, voice_id: 'd'.repeat(32) }] })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ voice_id: voiceId, sessions: [{ ...session, qc: { ...session.qc, clipped_fraction: 2 } }] })))
  vi.stubGlobal('fetch', fetch)
  await expect(api.listRecordingSessions(voiceId)).rejects.toThrow()
  await expect(api.listRecordingSessions(voiceId)).rejects.toThrow('Invalid RecordingSessionsResponse')
})
it('submits training inspection with the declared register alias', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(session)))
  vi.stubGlobal('fetch', fetch)
  await expect(api.inspectRecording(voiceId, { purpose: 'training', filename: 'take.wav', labels: session.labels })).resolves.toEqual(session)
  expect(fetch.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ purpose: 'training', filename: 'take.wav', labels: session.labels }))
})
it('rejects traversal and unexpected returned purpose before showing a result', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...session, purpose: 'held_out' })))
  vi.stubGlobal('fetch', fetch)
  await expect(api.inspectRecording(voiceId, { purpose: 'training', filename: '../private.wav' })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
  await expect(api.inspectRecording(voiceId, { purpose: 'training', filename: 'take.wav' })).rejects.toThrow()
})
