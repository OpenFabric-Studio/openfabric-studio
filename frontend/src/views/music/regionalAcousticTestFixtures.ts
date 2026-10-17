import { parseRegionalAcousticJob, parseRegionalDatasetReview } from '../../api/generated'

export function acousticReviewFixture(datasetId = 'd'.repeat(32), datasetRevision = 0) {
  return parseRegionalDatasetReview({ revision: 1, sha256: 'e'.repeat(64), created_at: '2026-10-17T10:00:00Z', request: {
    dataset_id: datasetId, dataset_revision: datasetRevision, expected_revision: 0, tradition: 'Fixture',
    recordings: ['a', 'b'].map(id => ({ id: id.repeat(32), source: { dataset_id: datasetId, revision: datasetRevision, sample_id: id },
      source_sha256: id.repeat(64), caption: `Recording ${id}` })),
    excerpts: ['a', 'b'].map(id => ({ id: id.repeat(32), recording_id: id.repeat(32), start_frame: 0, end_frame: 80000 })),
  } })
}
export function acousticJobFixture(datasetId = 'd'.repeat(32), datasetRevision = 0, auditions = false) {
  const review = acousticReviewFixture(datasetId, datasetRevision)
  const job = parseRegionalAcousticJob({ id: 'f'.repeat(32), request: { dataset_id: review.request.dataset_id,
    review_revision: review.revision, review_sha256: review.sha256, submission_key: 'c'.repeat(32) }, status: 'done',
    created_at: review.created_at, result: { sha256: 'd'.repeat(64), report: { configuration_sha256: 'e'.repeat(64),
      selection_sha256: 'f'.repeat(64), excerpts: ['a', 'b'].map(id => ({ excerpt_id: id.repeat(32), recording_id: id.repeat(32),
        source_sha256: id.repeat(64), decoded_pcm_sha256: id.repeat(64), sample_rate_hz: 8000, channels: 1,
        start_frame: 0, end_frame: 80000, coverage: 'screened', landmark_count: 100 })), candidates: [{ id: 'c'.repeat(64),
        left_excerpt_id: 'a'.repeat(32), right_excerpt_id: 'b'.repeat(32), left_channel: 0, right_channel: 0,
        left_start_frame: 8000, left_end_frame: 40000, right_start_frame: 8000, right_end_frame: 40000,
        aligned_seconds: 4, matched_landmarks: 20, distinct_anchors: 8, spectral_agreement: .9 }] } } })
  if (auditions) {
    const candidate = job.result?.report.candidates?.[0]
    if (!candidate) throw new Error('Missing fixture pair')
    const clip = {sha256: 'e'.repeat(64), bytes: 128088, sample_rate_hz: 8000, channels: 1, frames: 32000, source_start_frame: 8000, source_end_frame: 40000}
    candidate.left_audition = {...clip, sample_rate_hz: 8000, channels: 1}
    candidate.right_audition = {...clip, sample_rate_hz: 8000, channels: 1, sha256: 'f'.repeat(64)}
  }
  return job
}
