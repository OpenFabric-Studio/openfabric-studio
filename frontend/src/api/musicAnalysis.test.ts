import { afterEach, expect, it, vi } from 'vitest'
import * as api from './musicAnalysis'
import type { AnalysisResult, MusicAnalysisJob, MusicAnalysisOverrides, MusicAnalysisRequest } from './generated'

const source: MusicAnalysisRequest['source'] = { kind: 'version', track_id: 42, version_id: 'a'.repeat(32) }
const job: MusicAnalysisJob = { id: 'b'.repeat(32), source, status: 'queued', created_at: '2026-10-17T10:00:00Z' }
const result: AnalysisResult = { id: 'c'.repeat(32), source, audio_sha256: 'd'.repeat(64), sample_rate_hz: 48000, channels: 2, duration_frames: 480000, duration_sec: 10, analysis_sample_rate_hz: 16000, hop_sec: .1, analyzer_revision: 'test-v1', configuration_sha256: 'e'.repeat(64), loudness: { duration_sec: 10, source_sha256: 'd'.repeat(64) }, created_at: job.created_at }
afterEach(() => vi.restoreAllMocks())

it('starts explicit source-bound analysis and forwards cancellation ownership', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(job)))
  const signal = new AbortController().signal
  await expect(api.startMusicAnalysis({ source }, signal)).resolves.toEqual(job)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }), signal })
})

it('loads the retained-copy and scratch forecast without starting analysis', async () => {
  const preview = { source_bytes: 1000, retained_bytes: 1000, temporary_bytes: 2000, required_free_bytes: 3000, free_bytes: 4000, can_start: true }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(preview)))
  const signal = new AbortController().signal
  await expect(api.previewMusicAnalysis({ source }, signal)).resolves.toEqual(preview)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music/analysis/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }), signal })
})

it('rejects malformed storage forecasts at the generated response boundary', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ source_bytes: 1000, retained_bytes: -1, temporary_bytes: 2000, required_free_bytes: 3000, free_bytes: 4000, can_start: true })))
  await expect(api.previewMusicAnalysis({ source })).rejects.toThrow()
})

it('validates bounded list responses and safe identifiers before sending requests', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify([job]))).mockResolvedValueOnce(new Response(JSON.stringify(Array.from({ length: 101 }, () => job))))
  await expect(api.listMusicAnalysis()).resolves.toEqual([job])
  await expect(api.listMusicAnalysis()).rejects.toThrow()
  expect(() => api.getMusicAnalysis('../private')).toThrow()
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('requests exact-source history before the server applies its bounded result limit', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([job])))
  const signal = new AbortController().signal
  await expect(api.listMusicAnalysis(source, signal)).resolves.toEqual([job])
  const query = new URLSearchParams({ source: JSON.stringify(source) })
  expect(fetch).toHaveBeenCalledExactlyOnceWith(`/api/music/analysis?${query}`, { signal })
})

it('rejects foreign-source history returned for an exact-source request', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([{ ...job, source: { ...source, track_id: 43 } }])))
  await expect(api.listMusicAnalysis(source)).rejects.toThrow()
})

it.each(['cancel', 'retry'])('sends the %s action to the owned job', async action => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(job)))
  const signal = new AbortController().signal
  await (action === 'cancel' ? api.cancelMusicAnalysis : api.retryMusicAnalysis)(job.id, signal)
  expect(fetch).toHaveBeenCalledExactlyOnceWith(`/api/music/analysis/${job.id}/${action}`, { method: 'POST', signal })
})

it('patches the result analysis ID and current revision even when it differs from the job ID', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...result, override_revision: 3 })))
  const signal = new AbortController().signal
  const request: MusicAnalysisOverrides = { analysis_id: result.id, expected_revision: 2, meter: { status: 'known', beats_per_bar: 7, method: 'manual', confidence: null } }
  await expect(api.saveMusicAnalysisOverrides(request, signal)).resolves.toMatchObject({ override_revision: 3 })
  expect(fetch).toHaveBeenCalledExactlyOnceWith(`/api/music/analysis/${result.id}/overrides`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal })
})

it('rejects responses for another job or source and malformed analysis values', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ ...job, id: 'f'.repeat(32) }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...job, source: { ...source, track_id: 43 } }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...job, progress: { current: -1, total: 2, unit: 'windows' } })))
  await expect(api.getMusicAnalysis(job.id)).rejects.toThrow()
  await expect(api.startMusicAnalysis({ source })).rejects.toThrow()
  await expect(api.getMusicAnalysis(job.id)).rejects.toThrow()
})

it.each([
  { ...job, source: { track_id: 42, version_id: 'a'.repeat(32) } },
  { ...job, id: 'b'.repeat(32) + '\n' },
  { ...job, source: { kind: 'version', track_id: 42, version_id: 'a'.repeat(32) + '\n' } },
  { ...job, source: { kind: 'stem', track_id: 42, stem: 'vocals', sha256: 'a'.repeat(64) + '\n' } },
])('rejects untrusted response identities with a missing discriminator or trailing newline', async invalid => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([invalid])))
  await expect(api.listMusicAnalysis()).rejects.toThrow()
})

it('rejects trailing-newline route identifiers before making a request', () => {
  const fetch = vi.spyOn(globalThis, 'fetch')
  expect(() => api.getMusicAnalysis('a'.repeat(32) + '\n')).toThrow()
  expect(fetch).not.toHaveBeenCalled()
})
