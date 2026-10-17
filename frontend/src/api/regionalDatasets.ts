import { apiFetch } from './http'
import { parseRegionalAcousticScanRequest, parseRegionalAcousticJob, parseRegionalAcousticJobs } from './generated'
import type { RegionalAcousticJob, RegionalAcousticScanRequest } from './generated'
import { parseRegionalDatasetInspectionRequest, parseRegionalRecordingInspection, parseRegionalDatasetReview,
  parseRegionalDatasetReviewRequest, parseRegionalManifestSelection, parseRegionalDatasetPreview,
  parseRegionalPreparationRequest, parseRegionalPreparationJob, parseRegionalPreparationJobs,
  parseRegionalPreparedSelection, parseRegionalTrainingSelection, parseRegionalTrainingSubmission,
  parseStableLoraTrainingJob, parseRegionalEvaluationRequest, parseRegionalComparisonPlan,
  parseRegionalComparisonState, parseRegionalComparisons, parseRegionalListeningReview, parseRegionalBlindSessionRequest,
  parseRegionalBlindSession, parseRegionalBlindReview, parseRegionalBlindReveal, parseRegionalModelCard } from './generated'
import type { RegionalDatasetInspectionRequest, RegionalDatasetReviewRequest, RegionalManifestSelection,
  RegionalPreparationRequest, RegionalPreparedSelection, RegionalTrainingSubmission, RegionalComparisonState,
  RegionalEvaluationRequest, RegionalListeningReview, RegionalBlindSessionRequest, RegionalBlindReview } from './generated'

const base = '/api/music/regional-datasets'
function id(value: string): string {
  if (value.length !== 32 || !/^[a-f0-9]{32}$/.test(value)) throw new TypeError('Invalid regional identifier')
  return value
}
function write<T>(path: string, body: object, parse: (value: unknown) => T, signal?: AbortSignal, method = 'POST'): Promise<T> {
  return apiFetch(`${base}${path}`, { method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, parse)
}
function acousticJob(value: unknown, identifier: string): RegionalAcousticJob {
  const result = parseRegionalAcousticJob(value)
  if (result.id !== identifier) throw new TypeError('Mismatched acoustic scan')
  return result
}
export function startRegionalAcousticScan(body: RegionalAcousticScanRequest, signal?: AbortSignal) {
  const request = parseRegionalAcousticScanRequest(body)
  return write('/acoustic-scans', request, value => {
    const result = parseRegionalAcousticJob(value)
    if (result.request.dataset_id !== request.dataset_id || result.request.review_revision !== request.review_revision
      || result.request.review_sha256 !== request.review_sha256 || result.request.submission_key !== request.submission_key) throw new TypeError('Mismatched acoustic submission')
    return result
  }, signal)
}
export function listRegionalAcousticScans(datasetId: string, signal?: AbortSignal) {
  const checked = id(datasetId)
  return apiFetch(`${base}/acoustic-scans?dataset_id=${checked}`, { signal }, value => {
    const result = parseRegionalAcousticJobs(value)
    if (result.jobs.some(job => job.request.dataset_id !== checked) || new Set(result.jobs.map(job => job.id)).size !== result.jobs.length) throw new TypeError('Mismatched acoustic history')
    return result
  })
}
export function getRegionalAcousticScan(identifier: string, signal?: AbortSignal) {
  const checked = id(identifier)
  return apiFetch(`${base}/acoustic-scans/${checked}`, { signal }, value => acousticJob(value, checked))
}
export function cancelRegionalAcousticScan(identifier: string, signal?: AbortSignal) {
  const checked = id(identifier)
  return write(`/acoustic-scans/${checked}/cancel`, {}, value => acousticJob(value, checked), signal)
}
export function regionalAcousticAuditionUrl(scanId: string, candidateId: string, side: 'left' | 'right'): string {
  if (!/^[a-f0-9]{64}$/.test(candidateId) || candidateId.length !== 64 || !['left', 'right'].includes(side)) throw new TypeError('Invalid acoustic audition')
  return `${base}/acoustic-scans/${id(scanId)}/candidates/${candidateId}/audio/${side}`
}
function boundState(value: unknown, identifier?: string): RegionalComparisonState {
  const state = parseRegionalComparisonState(value), spec = state.plan.spec
  if (identifier !== undefined && spec.id !== identifier || state.arms.length !== 2 || new Set(state.arms.map(arm => arm.arm)).size !== 2
    || spec.adapter.id !== spec.request.adapter_id || spec.adapter.sha256 !== spec.request.adapter_sha256
    || spec.training_receipt.job_id !== spec.training_job_id || spec.adapter.source_job_id !== spec.training_job_id
    || spec.training_request.samples.some(sample => !sample.regional_manifest
      || sample.regional_manifest.manifest_id !== spec.request.manifest_id
      || sample.regional_manifest.manifest_sha256 !== spec.request.manifest_sha256
      || spec.held_out_excerpt_ids.includes(sample.regional_manifest.excerpt_id))) throw new TypeError('Mismatched regional comparison')
  for (const arm of state.arms) {
    const job = arm.job
    if (!job) continue
    if (job.request.prompt !== spec.request.request.prompt || job.request.seed !== spec.request.request.seed
      || job.request.variant !== spec.request.request.variant || job.request.duration_seconds !== spec.request.request.duration_seconds
      || job.request.conditioning != null || arm.arm === 'base' && job.adapter != null
      || arm.arm === 'adapter' && (job.adapter?.sha256 !== spec.adapter.sha256 || job.adapter.id !== spec.adapter.id)) throw new TypeError('Mismatched comparison job')
  }
  return state
}
export async function inspectRegionalRecording(body: RegionalDatasetInspectionRequest, signal?: AbortSignal) {
  const request = parseRegionalDatasetInspectionRequest(body)
  return write('/inspect', request, value => {
    const response = parseRegionalRecordingInspection(value)
    if (response.recording_id !== request.recording_id) throw new TypeError('Mismatched regional recording')
    return response
  }, signal)
}
export async function loadRegionalReview(datasetId: string, signal?: AbortSignal) {
  const checked = id(datasetId)
  return apiFetch(`${base}/reviews/${checked}`, { signal }, value => {
    const response = parseRegionalDatasetReview(value)
    if (response.request.dataset_id !== checked) throw new TypeError('Mismatched regional review')
    return response
  })
}
export function saveRegionalReview(body: RegionalDatasetReviewRequest, signal?: AbortSignal) {
  const request = parseRegionalDatasetReviewRequest(body)
  return write('/reviews', request, value => {
    const response = parseRegionalDatasetReview(value)
    if (response.request.dataset_id !== request.dataset_id || response.revision !== request.expected_revision + 1) throw new TypeError('Mismatched regional review')
    return response
  }, signal, 'PUT')
}
export function previewRegionalDataset(body: RegionalManifestSelection, signal?: AbortSignal) {
  const request = parseRegionalManifestSelection(body)
  return write('/preview', request, value => {
    const response = parseRegionalDatasetPreview(value)
    if (response.review_revision !== request.review_revision || response.review_sha256 !== request.review_sha256) throw new TypeError('Mismatched regional preview')
    return response
  }, signal)
}
export function prepareRegionalDataset(body: RegionalPreparationRequest, signal?: AbortSignal) {
  const request = parseRegionalPreparationRequest(body)
  return write('/preparations', request, value => {
    const response = parseRegionalPreparationJob(value)
    if (response.request.submission_key !== request.submission_key || response.request.review_sha256 !== request.review_sha256
      || response.request.dataset_id !== request.dataset_id) throw new TypeError('Mismatched preparation')
    return response
  }, signal)
}
export async function getRegionalPreparation(identifier: string, signal?: AbortSignal) {
  const checked = id(identifier)
  return apiFetch(`${base}/preparations/${checked}`, { signal }, value => {
    const response = parseRegionalPreparationJob(value)
    if (response.id !== checked) throw new TypeError('Mismatched preparation')
    return response
  })
}
export function listRegionalPreparations(datasetId: string, signal?: AbortSignal) {
  const checked = id(datasetId)
  return apiFetch(`${base}/preparations?dataset_id=${checked}`, { signal }, value => {
    const response = parseRegionalPreparationJobs(value)
    if (response.jobs.some(job => job.request.dataset_id !== checked)) throw new TypeError('Mismatched preparation history')
    return response
  })
}
export async function controlRegionalPreparation(identifier: string, action: 'cancel' | 'retry-save', signal?: AbortSignal) {
  const checked = id(identifier)
  return apiFetch(`${base}/preparations/${checked}/${action}`, { method: 'POST', signal }, value => {
    const response = parseRegionalPreparationJob(value)
    if (response.id !== checked) throw new TypeError('Mismatched preparation')
    return response
  })
}
export function regionalExcerptUrl(identifier: string, excerptId: string): string { return `${base}/preparations/${id(identifier)}/excerpts/${id(excerptId)}` }
export function regionalTrainingSelection(body: RegionalPreparedSelection, signal?: AbortSignal) {
  const request = parseRegionalPreparedSelection(body)
  return write('/training-selection', request, value => {
    const response = parseRegionalTrainingSelection(value)
    if (response.manifest_id !== request.manifest_id || response.manifest_sha256 !== request.manifest_sha256
      || response.samples.some(sample => !sample.regional_manifest || sample.regional_manifest.manifest_id !== request.manifest_id
        || sample.regional_manifest.manifest_sha256 !== request.manifest_sha256)) throw new TypeError('Mismatched training manifest')
    return response
  }, signal)
}
export function submitRegionalTraining(body: RegionalTrainingSubmission, signal?: AbortSignal) {
  return write('/training', parseRegionalTrainingSubmission(body), parseStableLoraTrainingJob, signal)
}
export function createRegionalComparison(body: RegionalEvaluationRequest, signal?: AbortSignal) {
  const request = parseRegionalEvaluationRequest(body)
  return write('/comparisons', request, value => {
    const response = parseRegionalComparisonPlan(value)
    if (response.spec.request.submission_key !== request.submission_key || response.spec.request.adapter_sha256 !== request.adapter_sha256
      || response.spec.request.manifest_sha256 !== request.manifest_sha256) throw new TypeError('Mismatched comparison specification')
    return response
  }, signal)
}
export async function getRegionalComparison(identifier: string, signal?: AbortSignal) {
  const checked = id(identifier)
  return apiFetch(`${base}/comparisons/${checked}`, { signal }, value => boundState(value, checked))
}
export function listRegionalComparisons(manifestId: string, signal?: AbortSignal) {
  const checked = id(manifestId)
  return apiFetch(`${base}/comparisons?manifest_id=${checked}`, { signal }, value => {
    const response = parseRegionalComparisons(value)
    return { comparisons: response.comparisons.map(state => {
      if (state.plan.spec.request.manifest_id !== checked) throw new TypeError('Mismatched comparison history')
      return boundState(state)
    }) }
  })
}
export async function controlRegionalArm(identifier: string, arm: 'base' | 'adapter', action: 'submit' | 'collect', signal?: AbortSignal) {
  const checked = id(identifier)
  return apiFetch(`${base}/comparisons/${checked}/arms/${arm}/${action}`, { method: 'POST', signal }, value => boundState(value, checked))
}
export async function saveRegionalListeningReview(identifier: string, body: RegionalListeningReview, signal?: AbortSignal) {
  const checked = id(identifier)
  return write(`/comparisons/${checked}/reviews`, parseRegionalListeningReview(body), value => boundState(value, checked), signal)
}
function reviewer(value: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$/.test(value)) throw new TypeError('Invalid reviewer reference')
  return value
}
export async function openRegionalBlindSession(identifier: string, body: RegionalBlindSessionRequest, signal?: AbortSignal) {
  const checked = id(identifier)
  const request = parseRegionalBlindSessionRequest(body)
  reviewer(request.reviewer_reference)
  return write(`/comparisons/${checked}/blind-sessions`, request, value => {
    const session = parseRegionalBlindSession(value)
    if (session.comparison_id !== checked || session.reviewer_reference !== request.reviewer_reference
      || session.clips.map(clip => clip.label).join() !== 'a,b'
      || session.clips.some(clip => !clip.audio_url.endsWith(`/blind/${request.reviewer_reference}/audio/${clip.label}`))) throw new TypeError('Mismatched blind session')
    return session
  }, signal)
}
const modelCardNotices = ['quality_unverified', 'metrics_unavailable', 'listening_not_a_panel', 'generation_runtime_unverified', 'distribution_not_approved']
export async function getRegionalModelCard(identifier: string, signal?: AbortSignal) {
  const checked = id(identifier)
  return apiFetch(`${base}/comparisons/${checked}/model-card`, { signal }, value => {
    const card = parseRegionalModelCard(value)
    if (card.comparison_id !== checked || card.quality_status !== 'unverified' || card.distribution_status !== 'not_approved'
      || card.notices.map(item => item.code).join() !== modelCardNotices.join()) throw new TypeError('Mismatched regional model card')
    return card
  })
}
export async function saveRegionalBlindReview(identifier: string, body: RegionalBlindReview, signal?: AbortSignal) {
  const checked = id(identifier)
  const request = parseRegionalBlindReview(body)
  reviewer(request.reviewer_reference)
  return write(`/comparisons/${checked}/blind-reviews`, request, value => {
    const reveal = parseRegionalBlindReveal(value)
    if (reveal.comparison_id !== checked || reveal.reviewer_reference !== request.reviewer_reference
      || reveal.quality_status !== 'unverified' || new Set([reveal.label_a_arm, reveal.label_b_arm]).size !== 2) throw new TypeError('Mismatched blind review')
    return reveal
  }, signal)
}
