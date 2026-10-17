import { apiFetch } from './http'
import { parseMashupCreateRequest, parseMashupEditPlanRequest, parseMashupGeneratePlanRequest, parseMashupJob, parseMashupJobRequest, parseMashupJobs, parseMashupSourcePreview, parseMashupSourcePreviewRequest, parseMashupState, parseMashupStates, parseMashupStoragePreview, parseMashupTimingUpdate, parseMusicProjectPublication } from './generated'
import type { MashupCreateRequest, MashupEditPlanRequest, MashupGeneratePlanRequest, MashupJob, MashupJobRequest, MashupSourcePreviewRequest, MashupState, MashupTimingUpdate } from './generated'

const base = '/api/mashups'
function idPath(id: string): string { if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid mashup identity'); return id }
function path(id: string): string { return `${base}/${idPath(id)}` }
function jobPath(id: string): string { return `${base}/jobs/${idPath(id)}` }
function json(body: object, signal?: AbortSignal, method = 'POST'): RequestInit { return { method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } }
function owned(value: unknown, id: string): MashupState { const result = parseMashupState(value); if (result.id !== id) throw new TypeError('Mismatched mashup'); return result }
function ownedJob(value: unknown, id: string): MashupJob { const result = parseMashupJob(value); if (result.id !== id || result.manifest && (result.manifest.job_id !== id || result.manifest.mashup_id !== result.mashup_id || result.manifest.mashup_revision !== result.mashup_revision)) throw new TypeError('Mismatched mashup job'); return result }
export const listMashups = (signal?: AbortSignal) => apiFetch(base, { signal }, parseMashupStates)
export const getMashup = (id: string, signal?: AbortSignal) => apiFetch(path(id), { signal }, value => owned(value, id))
export const previewMashupSource = (request: MashupSourcePreviewRequest, signal?: AbortSignal) => apiFetch(`${base}/source-preview`, json(parseMashupSourcePreviewRequest(request), signal), parseMashupSourcePreview)
export const createMashup = (request: MashupCreateRequest, signal?: AbortSignal) => apiFetch(base, json(parseMashupCreateRequest(request), signal), parseMashupState)
export const updateMashupTiming = (id: string, request: MashupTimingUpdate, signal?: AbortSignal) => apiFetch(`${path(id)}/timing`, json(parseMashupTimingUpdate(request), signal, 'PATCH'), value => owned(value, id))
export const generateMashupPlan = (id: string, request: MashupGeneratePlanRequest, signal?: AbortSignal) => apiFetch(`${path(id)}/plan`, json(parseMashupGeneratePlanRequest(request), signal), value => owned(value, id))
export const editMashupPlan = (id: string, request: MashupEditPlanRequest, signal?: AbortSignal) => apiFetch(`${path(id)}/plan`, json(parseMashupEditPlanRequest(request), signal, 'PATCH'), value => owned(value, id))
export const previewMashupStorage = (id: string, request: MashupJobRequest, signal?: AbortSignal) => apiFetch(`${path(id)}/storage-preview`, json(parseMashupJobRequest(request), signal), parseMashupStoragePreview)
export const listMashupJobs = (id: string, signal?: AbortSignal) => apiFetch(`${path(id)}/jobs`, { signal }, value => { const result = parseMashupJobs(value); if (result.jobs.some(job => job.mashup_id !== id)) throw new TypeError('Mismatched mashup jobs'); for (const job of result.jobs) ownedJob(job, job.id); return result })
export const getMashupJob = (id: string, signal?: AbortSignal) => apiFetch(jobPath(id), { signal }, value => ownedJob(value, id))
export const startMashupRender = (id: string, request: MashupJobRequest, signal?: AbortSignal) => apiFetch(`${path(id)}/${request.mode === 'preview' ? 'preview' : 'render'}`, json(parseMashupJobRequest(request), signal), value => { const result = parseMashupJob(value); if (result.mashup_id !== id || result.mashup_revision !== request.expected_revision || result.kind !== (request.mode ?? 'full')) throw new TypeError('Mismatched mashup render'); return ownedJob(value, result.id) })
export const cancelMashup = (id: string, signal?: AbortSignal) => apiFetch(`${path(id)}/cancel`, { method: 'POST', signal }, value => owned(value, id))
export const retryMashupPreparation = (id: string, signal?: AbortSignal) => apiFetch(`${path(id)}/retry-preparation`, { method: 'POST', signal }, value => owned(value, id))
export const cancelMashupJob = (id: string, signal?: AbortSignal) => apiFetch(`${jobPath(id)}/cancel`, { method: 'POST', signal }, value => ownedJob(value, id))
export const retryMashupSave = (id: string, signal?: AbortSignal) => apiFetch(`${jobPath(id)}/retry-save`, { method: 'POST', signal }, value => ownedJob(value, id))
export const publishMashupProject = (job: MashupJob, signal?: AbortSignal) => apiFetch(`${jobPath(job.id)}/project`, { method: 'POST', signal }, value => { const result = parseMusicProjectPublication(value); if (result.provenance.workflow !== 'mashup' || result.provenance.workflow_id !== job.mashup_id || result.provenance.workflow_revision !== job.mashup_revision) throw new TypeError('Mismatched mashup project'); return result })
export const mashupArtifactUrl = (jobId: string, artifactId: string): string => `${jobPath(jobId)}/artifacts/${idPath(artifactId)}`
