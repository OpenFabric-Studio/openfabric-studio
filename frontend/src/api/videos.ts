import { stats } from './aceStep'
import { trainingStatus } from './aceStepTraining'
import { apiFetch, ApiError } from './http'
import { i18n } from '../i18n'
import { getMidiStatus } from './midi'
import { getSeparationStatus } from './stems'
import { applyStatus, isVoiceActive, listVoices } from './voices'

import { parseVideosResponse, parseVideoActivityResponse } from './contracts'
import { parseAudiobookPassagesResponse, parseCreateDialogueReelRequest, parseRefreshDialogueCueRequest, parseCharacterDatasetReview, parseReviewCharacterAdapterRequest } from './contracts'
import type { AudiobookPassagesResponse, CreateDialogueReelRequest, RefreshDialogueCueRequest, CharacterDatasetReview, ReviewCharacterAdapterRequest } from './contracts'
import type { VideoJobResponse, VideoPlanResponse, VideoShot as BackendVideoShot } from './contracts'
import { parseVideoReadinessResponse, parseVideoProject, parseVideoProjectsResponse, parseCreateVideoProjectRequest, parseUpdateVideoProjectRequest,
  parseVideoRenderRequest, parseVideoRevisionRequest, parseApproveVideoVariantRequest, parseVideoExportRequest,
  parseVideoSpeechLineRequest, parseApplyVideoCharacterRequest, parseApplyVideoCharacterAdapterRequest, parseVideoCharacter, parseVideoCharactersResponse, parseVideoCharacterTrainingJob, parseVideoCharacterTrainingResponse, parseVideoCharacterTrainerStatus, parseVideoCharacterTrainerSettingsRequest } from './contracts'
import type { VideoReadinessResponse, VideoProject, VideoProjectsResponse, CreateVideoProjectRequest, UpdateVideoProjectRequest,
  VideoRenderRequest, VideoRevisionRequest, ApproveVideoVariantRequest, VideoExportRequest, VideoSpeechLineRequest,
  ApplyVideoCharacterRequest, VideoCharacter, VideoCharactersResponse, VideoCharacterTrainingJob, VideoCharacterTrainingResponse, VideoCharacterTrainerStatus } from './contracts'

export type VideoJob = VideoJobResponse
export type VideoStatus = VideoJob['status']
export type VideoPhase = VideoJob['phase']
export type VideoShot = BackendVideoShot
export type VideoPlan = VideoPlanResponse

let activityMissing = false

const ACTIVE: VideoStatus[] = ['queued', 'running']

export function isVideoActive(status: string | undefined): boolean {
  return ACTIVE.some((candidate) => candidate === status)
}

export function videoErrorText(code: string, _detail = ''): string {
  const key = `video.err.${code}`
  return String(i18n.global.t(code && i18n.global.te(key) ? key : 'video.err.unknown'))
}

export function listVideos(): Promise<{ videos: VideoJob[] }> {
  return apiFetch('/api/videos', undefined, parseVideosResponse)
}

export function videoActivity(): Promise<{ busy: boolean }> {
  return apiFetch('/api/videos/activity', undefined, parseVideoActivityResponse)
}

function jobActive(status: string | undefined): boolean {
  return status === 'queued' || status === 'running'
}

async function legacyOtherWorkBusy(trackIds: number[]): Promise<boolean> {
  const [song, training, voices] = await Promise.all([
    stats().catch(() => null),
    trainingStatus().catch(() => null),
    listVoices().catch(() => []),
  ])
  const jobs = song?.jobs
  if ((jobs?.queued ?? 0) > 0 || (jobs?.running ?? 0) > 0 || (song?.queue_size ?? 0) > 0) return true
  if (training?.is_training) return true
  if (voices.some((voice) => isVoiceActive(voice.status))) return true
  const rows = await Promise.all(trackIds.map(async (trackId) => {
    const [apply, stems, midi] = await Promise.all([
      applyStatus(trackId).catch(() => null),
      getSeparationStatus(trackId).catch(() => null),
      getMidiStatus(trackId).catch(() => null),
    ])
    if (jobActive(apply?.status) || jobActive(stems?.status)) return true
    return Object.values(midi?.sources ?? {}).some((source) => jobActive(source.status))
  }))
  return rows.some(Boolean)
}

export async function otherWorkBusy(trackIds: number[]): Promise<boolean> {
  if (!activityMissing) {
    try {
      const row = await videoActivity()
      return Boolean(row.busy)
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 404) throw err
      activityMissing = true
    }
  }
  return legacyOtherWorkBusy(trackIds)
}

export async function deleteVideo(id: string): Promise<void> {
  await apiFetch(`/api/videos/${id}`, { method: 'DELETE' })
}

export function videoRequestError(err: unknown): string {
  return err instanceof ApiError && i18n.global.te(`video.err.${err.message}`) ? err.message : 'unknown'
}

function projectPath(id: string): string {
  return `/api/videos/projects/${encodeURIComponent(id)}`
}

async function projectPost(id: string, action: string, body: unknown, signal?: AbortSignal): Promise<VideoProject> {
  return apiFetch(`${projectPath(id)}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }, parseVideoProject)
}

export function listVideoProjects(signal?: AbortSignal): Promise<VideoProjectsResponse> {
  return apiFetch('/api/videos/projects', { signal }, parseVideoProjectsResponse)
}

export function videoReadiness(signal?: AbortSignal): Promise<VideoReadinessResponse> {
  return apiFetch('/api/videos/readiness', { signal }, parseVideoReadinessResponse)
}

export function getVideoProject(id: string, signal?: AbortSignal): Promise<VideoProject> {
  return apiFetch(projectPath(id), { signal }, parseVideoProject)
}

export async function createVideoProject(body: CreateVideoProjectRequest, signal?: AbortSignal): Promise<VideoProject> {
  return apiFetch('/api/videos/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseCreateVideoProjectRequest(body)), signal }, parseVideoProject)
}

export async function updateVideoProject(id: string, body: UpdateVideoProjectRequest, signal?: AbortSignal): Promise<VideoProject> {
  return apiFetch(projectPath(id), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseUpdateVideoProjectRequest(body)), signal }, parseVideoProject)
}

export async function analyzeVideoProject(id: string, body: VideoRevisionRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'analyze', parseVideoRevisionRequest(body), signal)
}

export async function duplicateVideoProject(id: string, body: VideoRevisionRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'duplicate', parseVideoRevisionRequest(body), signal)
}

export async function uploadVideoReference(id: string, revision: number, file: File, signal?: AbortSignal): Promise<VideoProject> {
  parseVideoRevisionRequest({ revision })
  const form = new FormData()
  form.append('revision', String(revision))
  form.append('file', file)
  return apiFetch(`${projectPath(id)}/references`, { method: 'POST', body: form, signal }, parseVideoProject)
}

export async function uploadVideoSpeech(id: string, revision: number, file: File, signal?: AbortSignal): Promise<VideoProject> {
  parseVideoRevisionRequest({ revision })
  const form = new FormData()
  form.append('revision', String(revision))
  form.append('file', file)
  return apiFetch(`${projectPath(id)}/speech`, { method: 'POST', body: form, signal }, parseVideoProject)
}

export async function clearVideoSpeech(id: string, body: VideoRevisionRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'speech/clear', parseVideoRevisionRequest(body), signal)
}

export async function previewVideoProject(id: string, body: VideoRenderRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'preview', parseVideoRenderRequest(body), signal)
}

export async function renderVideoProject(id: string, body: VideoRenderRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'render', parseVideoRenderRequest(body), signal)
}

export async function resumeVideoProject(id: string, body: VideoRevisionRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'resume', parseVideoRevisionRequest(body), signal)
}

export async function cancelVideoProject(id: string, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'cancel', {}, signal)
}

export async function approveVideoVariant(id: string, shotId: string, body: ApproveVideoVariantRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, `shots/${encodeURIComponent(shotId)}/approve`, parseApproveVideoVariantRequest(body), signal)
}

export async function exportVideoProject(id: string, body: VideoExportRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'export', parseVideoExportRequest(body), signal)
}

export async function deleteVideoProject(id: string, signal?: AbortSignal): Promise<void> {
  await apiFetch(projectPath(id), { method: 'DELETE', signal })
}

export function speakVideoLine(id: string, body: VideoSpeechLineRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'speech/line', parseVideoSpeechLineRequest(body), signal)
}

export function listVideoCharacters(signal?: AbortSignal): Promise<VideoCharactersResponse> {
  return apiFetch('/api/videos/characters', { signal }, parseVideoCharactersResponse)
}

export async function createVideoCharacter(input: { name: string; voiceProfileId: string; consentConfirmed: boolean; still: File }, signal?: AbortSignal): Promise<VideoCharacter> {
  const form = new FormData()
  form.append('name', input.name)
  form.append('voice_profile_id', input.voiceProfileId)
  form.append('consent_confirmed', input.consentConfirmed ? 'true' : 'false')
  form.append('file', input.still, input.still.name)
  return apiFetch('/api/videos/characters', { method: 'POST', body: form, signal }, parseVideoCharacter)
}

export function applyVideoCharacter(id: string, body: ApplyVideoCharacterRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'character', parseApplyVideoCharacterRequest(body), signal)
}

export function characterTrainerStatus(signal?: AbortSignal): Promise<VideoCharacterTrainerStatus> {
  return apiFetch('/api/videos/character-training/status', { signal }, parseVideoCharacterTrainerStatus)
}

export function saveCharacterTrainer(command: string, signal?: AbortSignal): Promise<VideoCharacterTrainerStatus> {
  return apiFetch('/api/videos/character-training/trainer', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseVideoCharacterTrainerSettingsRequest({ command })), signal }, parseVideoCharacterTrainerStatus)
}

export function listCharacterTraining(signal?: AbortSignal): Promise<VideoCharacterTrainingResponse> {
  return apiFetch('/api/videos/character-training', { signal }, parseVideoCharacterTrainingResponse)
}

export async function startCharacterTraining(input: { name: string; consentConfirmed: boolean; files: File[]; review?: CharacterDatasetReview }, signal?: AbortSignal): Promise<VideoCharacterTrainingJob> {
  const form = new FormData()
  form.append('name', input.name)
  form.append('consent_confirmed', input.consentConfirmed ? 'true' : 'false')
  for (const file of input.files) form.append('files', file, file.name)
  if (input.review) form.append('dataset_review', JSON.stringify(parseCharacterDatasetReview(input.review)))
  return apiFetch('/api/videos/character-training', { method: 'POST', body: form, signal }, parseVideoCharacterTrainingJob)
}

export function cancelCharacterTraining(id: string, signal?: AbortSignal): Promise<VideoCharacterTrainingJob> {
  return apiFetch(`/api/videos/character-training/${encodeURIComponent(id)}/cancel`, { method: 'POST', signal }, parseVideoCharacterTrainingJob)
}

export function applyCharacterAdapter(id: string, body: { revision: number; training_id: string }, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'character-adapter', parseApplyVideoCharacterAdapterRequest(body), signal)
}


export function createDialogueReel(body: CreateDialogueReelRequest, signal?: AbortSignal): Promise<VideoProject> {
  return apiFetch('/api/videos/dialogue-reels', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseCreateDialogueReelRequest(body)), signal }, parseVideoProject)
}

export function refreshDialogueCue(id: string, shotId: string, body: RefreshDialogueCueRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, `dialogue-cues/${encodeURIComponent(shotId)}`, parseRefreshDialogueCueRequest(body), signal)
}

export function undoVideoProject(id: string, body: VideoRevisionRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'undo', parseVideoRevisionRequest(body), signal)
}

export function redoVideoProject(id: string, body: VideoRevisionRequest, signal?: AbortSignal): Promise<VideoProject> {
  return projectPost(id, 'redo', parseVideoRevisionRequest(body), signal)
}

export function createCharacterComparison(id: string, signal?: AbortSignal): Promise<VideoCharacterTrainingJob> {
  return apiFetch(`/api/videos/character-training/${encodeURIComponent(id)}/comparison`, { method: 'POST', signal }, parseVideoCharacterTrainingJob)
}

export function reviewCharacterComparison(id: string, body: ReviewCharacterAdapterRequest, signal?: AbortSignal): Promise<VideoCharacterTrainingJob> {
  return apiFetch(`/api/videos/character-training/${encodeURIComponent(id)}/review`, { method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseReviewCharacterAdapterRequest(body)) }, parseVideoCharacterTrainingJob)
}


export function dialoguePassages(bookId: string, chapterIndex: number, signal?: AbortSignal): Promise<AudiobookPassagesResponse> {
  return apiFetch(`/api/audiobooks/${encodeURIComponent(bookId)}/chapters/${chapterIndex}/passages`, { signal }, parseAudiobookPassagesResponse)
}
