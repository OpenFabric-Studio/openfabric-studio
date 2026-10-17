import { afterEach, expect, it, vi } from 'vitest'
import * as api from './regionalDatasets'

afterEach(() => vi.restoreAllMocks())

it('binds inspections to recording identity and rejects malformed IDs before HTTP', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ recording_id: 'b'.repeat(32),
    source_sha256: 'c'.repeat(64), decoded_pcm_sha256: 'd'.repeat(64), source_bytes: 100, sample_rate_hz: 8000, channels: 1, frames: 800 })))
  await expect(api.inspectRegionalRecording({ recording_id: 'a'.repeat(32), source: {
    dataset_id: 'e'.repeat(32), revision: 0, sample_id: 'phrase' } })).rejects.toThrow()
  await expect(api.getRegionalPreparation('a'.repeat(32) + '\n')).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('refuses training samples from a different manifest instead of trusting the response schema alone', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ manifest_id: 'a'.repeat(32), manifest_sha256: 'b'.repeat(64),
    held_out_excerpts: 1, evaluation_sufficient: true, adapter_distribution_permitted: false, samples: [{ regional_manifest: {
      manifest_id: 'f'.repeat(32), manifest_sha256: 'b'.repeat(64), excerpt_id: 'c'.repeat(32) }, caption: 'Reviewed phrase' }] })))
  await expect(api.regionalTrainingSelection({ manifest_id: 'a'.repeat(32), manifest_sha256: 'b'.repeat(64) })).rejects.toThrow()
})

it('binds acoustic scans to their submission and dataset before displaying or replaying them', async () => {
  const request = { dataset_id: 'a'.repeat(32), review_revision: 1, review_sha256: 'b'.repeat(64), submission_key: 'c'.repeat(32) }
  const job = { id: 'd'.repeat(32), request, status: 'queued', created_at: '2026-10-17T10:00:00Z' }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(job)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...job, request: { ...request, submission_key: 'f'.repeat(32) } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ jobs: [{ ...job, request: { ...request, dataset_id: 'f'.repeat(32) } }] })))
  await expect(api.startRegionalAcousticScan(request)).resolves.toMatchObject(job)
  expect(fetch.mock.calls[0]?.[0]).toBe('/api/music/regional-datasets/acoustic-scans')
  await expect(api.startRegionalAcousticScan(request)).rejects.toThrow()
  await expect(api.listRegionalAcousticScans(request.dataset_id)).rejects.toThrow()
  expect(() => api.getRegionalAcousticScan('../escape')).toThrow()
})
