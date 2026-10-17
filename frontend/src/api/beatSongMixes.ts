import { apiFetch } from './http'
import { parseBeatSongMixDraft, parseBeatSongMixJob, parseBeatSongMixJobs, parseBeatSongMixPlan, parseBeatSongMixPreview, parseBeatSongMixStartRequest, parseBeatSongMixUpdate, parseMusicProjectPublication, parseSongSourceInfo, parseSongSourceRequest } from './generated'
import type { BeatSongMixDraft, BeatSongMixJob, BeatSongMixPlan, BeatSongMixStartRequest, BeatSongMixUpdate, SongSourceInfo, SongSourceRequest } from './generated'

const base = '/api/beat-song-mixes'
function id(value: string): string { if (value.length !== 32 || !/^[a-f0-9]{32}$/.test(value)) throw new TypeError('Invalid beat/song identity'); return value }
const planPath = (value: string) => `${base}/plans/${id(value)}`
const jobPath = (value: string) => `${base}/${id(value)}`
const json = (body: object, signal?: AbortSignal, method = 'POST'): RequestInit => ({ method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
function sourceKey(source: SongSourceRequest['source']): string {
  return source.kind === 'version' ? `version:${source.track_id}:${source.version_id}` : `beat:${source.beat_id}:${source.beat_revision}:${source.render_id}:${source.sha256}`
}
function checkedSource(source: SongSourceInfo): SongSourceInfo {
  if (source.source.kind === 'version' ? source.track_id !== source.source.track_id || source.version_id !== source.source.version_id : source.source.sha256 !== source.sha256) throw new TypeError('Mismatched beat/song source identity')
  return source
}
function checkedPlan(plan: BeatSongMixPlan): BeatSongMixPlan {
  for (const side of ['beat', 'song'] as const) {
    const source = checkedSource(plan[side]), input = plan.draft[side]
    if (sourceKey(source.source) !== sourceKey(input.source) || source.sha256 !== input.sha256) throw new TypeError('Mismatched beat/song plan source')
  }
  if (new Set(plan.lanes.map(lane => lane.id)).size !== plan.lanes.length || plan.lanes.some(lane => lane.control.lane_id !== lane.id)) throw new TypeError('Mismatched beat/song lane')
  return plan
}
function checkedPreview(value: unknown) { const result = parseBeatSongMixPreview(value); checkedPlan(result.plan); return result }
function ownedJob(value: unknown, expectedId?: string): BeatSongMixJob {
  const job = parseBeatSongMixJob(value), { plan, request, manifest } = job
  checkedPlan(plan)
  if (expectedId !== undefined && job.id !== expectedId || request.plan_id !== plan.id || request.expected_revision !== plan.revision || request.plan_sha256 !== plan.sha256 || manifest && (manifest.job_id !== job.id || manifest.plan_id !== plan.id || manifest.plan_revision !== plan.revision || manifest.plan_sha256 !== plan.sha256 || manifest.mode !== (request.mode ?? 'full'))) throw new TypeError('Mismatched beat/song job')
  if (manifest) {
    const expectedSources = [plan.beat, plan.song].map(source => `${sourceKey(source.source)}:${source.sha256}`).sort()
    const retainedSources = manifest.sources.map(source => { checkedSource(source); return `${sourceKey(source.source)}:${source.sha256}` }).sort()
    if (JSON.stringify(expectedSources) !== JSON.stringify(retainedSources) || new Set(manifest.artifacts.map(artifact => artifact.id)).size !== manifest.artifacts.length || manifest.lanes.some(lane => !plan.lanes.some(item => item.id === lane.lane_id) || !manifest.artifacts.some(artifact => artifact.id === lane.artifact_id && artifact.kind === 'processed' && artifact.lane_id === lane.lane_id) || !manifest.artifacts.some(artifact => artifact.id === lane.dry_artifact_id && artifact.kind === 'dry' && artifact.lane_id === lane.lane_id))) throw new TypeError('Mismatched beat/song artifacts')
  }
  return job
}
export const inspectBeatSongSource = (body: SongSourceRequest, signal?: AbortSignal) => {
  const request = parseSongSourceRequest(body)
  return apiFetch(`${base}/sources/inspect`, json(request, signal), value => {
    const result = checkedSource(parseSongSourceInfo(value))
    if (sourceKey(result.source) !== sourceKey(request.source)) throw new TypeError('Mismatched beat/song source')
    return result
  })
}
export const createBeatSongPlan = (body: BeatSongMixDraft, signal?: AbortSignal) => apiFetch(`${base}/plans`, json(parseBeatSongMixDraft(body), signal), checkedPreview)
export const getBeatSongPlan = (planId: string, signal?: AbortSignal) => apiFetch(planPath(planId), { signal }, value => {
  const result = checkedPlan(parseBeatSongMixPlan(value)); if (result.id !== planId) throw new TypeError('Mismatched beat/song plan'); return result
})
export const updateBeatSongPlan = (planId: string, body: BeatSongMixUpdate, signal?: AbortSignal) => {
  const request = parseBeatSongMixUpdate(body)
  return apiFetch(planPath(planId), json(request, signal, 'PATCH'), value => {
    const result = checkedPreview(value)
    if (result.plan.id !== planId || result.plan.revision !== request.expected_revision + 1) throw new TypeError('Mismatched beat/song revision')
    return result
  })
}
export const listBeatSongJobs = (signal?: AbortSignal) => apiFetch(base, { signal }, value => {
  const result = parseBeatSongMixJobs(value); result.jobs.forEach(job => ownedJob(job)); return result
})
export const getBeatSongJob = (jobId: string, signal?: AbortSignal) => apiFetch(jobPath(jobId), { signal }, value => ownedJob(value, jobId))
export const startBeatSongMix = (body: BeatSongMixStartRequest, signal?: AbortSignal) => {
  const request = parseBeatSongMixStartRequest(body)
  return apiFetch(base, json(request, signal), value => {
    const job = ownedJob(value)
    if (job.request.plan_id !== request.plan_id || job.request.expected_revision !== request.expected_revision || job.request.plan_sha256 !== request.plan_sha256 || job.request.submission_key !== request.submission_key || (job.request.mode ?? 'full') !== (request.mode ?? 'full') || (job.request.preview_start_sec ?? null) !== (request.preview_start_sec ?? null) || (job.request.preview_end_sec ?? null) !== (request.preview_end_sec ?? null)) throw new TypeError('Mismatched beat/song submission')
    return job
  })
}
function action(jobId: string, name: 'cancel' | 'retry-save', signal?: AbortSignal) { return apiFetch(`${jobPath(jobId)}/${name}`, json({}, signal), value => ownedJob(value, jobId)) }
export const cancelBeatSongMix = (jobId: string, signal?: AbortSignal) => action(jobId, 'cancel', signal)
export const retryBeatSongSave = (jobId: string, signal?: AbortSignal) => action(jobId, 'retry-save', signal)
export const publishBeatSongProject = (job: BeatSongMixJob, signal?: AbortSignal) => apiFetch(`${jobPath(job.id)}/project`, json({}, signal), value => {
  const result = parseMusicProjectPublication(value)
  if (result.provenance.workflow !== 'beat_song' || result.provenance.workflow_id !== job.plan.id || result.provenance.workflow_revision !== job.plan.revision) throw new TypeError('Mismatched beat/song project')
  return result
})
export const beatSongArtifactUrl = (jobId: string, artifactId: string): string => `${jobPath(jobId)}/artifacts/${id(artifactId)}`
