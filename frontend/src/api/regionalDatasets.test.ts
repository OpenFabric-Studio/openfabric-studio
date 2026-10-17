import { afterEach, expect, it, vi } from 'vitest'
import type { RegionalBlindReview } from './generated'
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

it('accepts a source-bound model card only when its status and notice order stay fixed', async () => {
  const comparisonId = 'a'.repeat(32)
  const notices = [
    { code: 'quality_unverified', text: 'Quality is unverified.' },
    { code: 'metrics_unavailable', text: 'FAD, CLAP, instrument classification and cultural authenticity were not measured.' },
    { code: 'listening_not_a_panel', text: 'Saved listening reviews are local records. They are not a recruited panel or a recommendation.' },
    { code: 'generation_runtime_unverified', text: 'The generation runtime identity was not measured.' },
    { code: 'distribution_not_approved', text: 'This card does not approve distribution, commercial use, or release.' } ]
  const card = { comparison_id: comparisonId, comparison_sha256: 'b'.repeat(64), quality_status: 'unverified', distribution_status: 'not_approved', notices,
    tradition: 'Fixture', manifest_id: 'c'.repeat(32), manifest_sha256: 'd'.repeat(64), training_recordings: 1, holdout_recordings: 1, excluded_recordings: 0,
    training_excerpts: 1, held_out_excerpts: 1, declared_adapter_distribution_permitted: false, declared_source_redistribution_permitted: false,
    adapter_id: 'e'.repeat(32), adapter_name: 'Fixture', adapter_sha256: 'f'.repeat(64), adapter_target: 'small-music', training_rank: 8, training_max_steps: 1,
    training_learning_rate: 0.0001, training_seed: 1, training_request_sha256: '1'.repeat(64), training_dataset_sha256: '2'.repeat(64), training_identity_status: 'unavailable',
    prompt: 'Held-out phrase', comparison_seed: 1, duration_seconds: 5, listener_reviews: 0, blind_reviews: 0, attributions: [], hashes: [] }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(card)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...card, quality_status: undefined })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...card, notices: [...notices].reverse() })))
  await expect(api.getRegionalModelCard(comparisonId)).resolves.toMatchObject({ distribution_status: 'not_approved', listener_reviews: 0 })
  await expect(api.getRegionalModelCard(comparisonId)).rejects.toThrow('Mismatched regional model card')
  await expect(api.getRegionalModelCard(comparisonId)).rejects.toThrow('Mismatched regional model card')
  expect(fetch.mock.calls[0]?.[0]).toBe(`/api/music/regional-datasets/comparisons/${comparisonId}/model-card`)
})

it('accepts an unlabeled session only when both clips and the reveal stay bound', async () => {
  const comparisonId = 'a'.repeat(32), reviewer = 'listener-one', planSha = 'b'.repeat(64), assignment = 'c'.repeat(64)
  const session = { comparison_id: comparisonId, comparison_sha256: planSha, reviewer_reference: reviewer, assignment_sha256: assignment, clips: [
    { label: 'a', output_sha256: 'd'.repeat(64), audio_url: `/api/music/regional-datasets/comparisons/${comparisonId}/blind/${reviewer}/audio/a` },
    { label: 'b', output_sha256: 'e'.repeat(64), audio_url: `/api/music/regional-datasets/comparisons/${comparisonId}/blind/${reviewer}/audio/b` } ] }
  const review: RegionalBlindReview = { comparison_sha256: planSha, assignment_sha256: assignment, label_a_sha256: 'd'.repeat(64), label_b_sha256: 'e'.repeat(64),
    reviewer_reference: reviewer, reviewed_at: '2026-10-17', tradition_scope: 'Fixture scope', preference: 'a', timbre: 3, phrasing: 3, rhythm: 3, coherence: 3, artifacts: 3, cultural_appropriateness: 3 }
  const reveal = { comparison_id: comparisonId, reviewer_reference: reviewer, label_a_arm: 'adapter', label_b_arm: 'base', decoded_preference: 'adapter', quality_status: 'unverified', blind_reviews: 1 }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(session)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...session, clips: [...session.clips].reverse() })))
    .mockResolvedValueOnce(new Response(JSON.stringify(reveal)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...reveal, quality_status: undefined, label_b_arm: 'adapter' })))
  await expect(api.openRegionalBlindSession(comparisonId, { reviewer_reference: reviewer })).resolves.toMatchObject({ assignment_sha256: assignment })
  await expect(api.openRegionalBlindSession(comparisonId, { reviewer_reference: reviewer })).rejects.toThrow('Mismatched blind session')
  await expect(api.saveRegionalBlindReview(comparisonId, review)).resolves.toMatchObject({ quality_status: 'unverified', blind_reviews: 1 })
  await expect(api.saveRegionalBlindReview(comparisonId, review)).rejects.toThrow('Mismatched blind review')
  expect(fetch.mock.calls[0]?.[0]).toBe(`/api/music/regional-datasets/comparisons/${comparisonId}/blind-sessions`)
  expect(String(fetch.mock.calls[0]?.[1]?.body)).not.toContain('/audio/base')
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
