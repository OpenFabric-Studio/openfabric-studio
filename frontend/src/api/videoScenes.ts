import { apiFetch } from './http'
import { parseVideoSceneReadiness, parseVideoSceneSourcesResponse, parseVideoSceneSource, parseVideoScenePreviewRequest,
  parseVideoScenePreview, parseVideoSceneCutsResponse, parseVideoSceneCutRequest, parseVideoSceneCut, parseDeletedVideoSceneCutResponse } from './contracts'
import type { VideoSceneReadiness, VideoSceneSourcesResponse, VideoSceneSource, VideoScenePreviewRequest,
  VideoScenePreview, VideoSceneCutsResponse, VideoSceneCutRequest, VideoSceneCut, DeletedVideoSceneCutResponse } from './contracts'
const path = '/api/videos/scenes'
function sourcePath(id: string) { return `${path}/sources/${encodeURIComponent(id)}` }
export function sceneReadiness(signal?: AbortSignal): Promise<VideoSceneReadiness> { return apiFetch(`${path}/readiness`, { signal }, parseVideoSceneReadiness) }
export function listSceneSources(signal?: AbortSignal): Promise<VideoSceneSourcesResponse> { return apiFetch(`${path}/sources`, { signal }, parseVideoSceneSourcesResponse) }
export function pickSceneSource(signal?: AbortSignal): Promise<VideoSceneSource> { return apiFetch(`${path}/sources/pick`, { method: 'POST', signal }, parseVideoSceneSource) }
export function uploadSceneSource(video: File, signal?: AbortSignal): Promise<VideoSceneSource> { const form = new FormData(); form.append('video', video, video.name); return apiFetch(`${path}/sources`, { method: 'POST', body: form, signal }, parseVideoSceneSource) }
export function getSceneSource(id: string, signal?: AbortSignal): Promise<VideoSceneSource> { return apiFetch(sourcePath(id), { signal }, parseVideoSceneSource) }
export function previewSceneSource(id: string, body: VideoScenePreviewRequest, signal?: AbortSignal): Promise<VideoScenePreview> { return apiFetch(`${sourcePath(id)}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseVideoScenePreviewRequest(body)), signal }, parseVideoScenePreview) }
export function listSceneCuts(id: string, signal?: AbortSignal): Promise<VideoSceneCutsResponse> { return apiFetch(`${sourcePath(id)}/cuts`, { signal }, parseVideoSceneCutsResponse) }
export function createSceneCut(id: string, body: VideoSceneCutRequest, signal?: AbortSignal): Promise<VideoSceneCut> { return apiFetch(`${sourcePath(id)}/cuts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseVideoSceneCutRequest(body)), signal }, parseVideoSceneCut) }
export function getSceneCut(id: string, cutId: string, signal?: AbortSignal): Promise<VideoSceneCut> { return apiFetch(`${sourcePath(id)}/cuts/${encodeURIComponent(cutId)}`, { signal }, parseVideoSceneCut) }
export function cancelSceneCut(id: string, cutId: string, signal?: AbortSignal): Promise<VideoSceneCut> { return apiFetch(`${sourcePath(id)}/cuts/${encodeURIComponent(cutId)}/cancel`, { method: 'POST', signal }, parseVideoSceneCut) }
export function deleteSceneCut(id: string, cutId: string, signal?: AbortSignal): Promise<DeletedVideoSceneCutResponse> { return apiFetch(`${sourcePath(id)}/cuts/${encodeURIComponent(cutId)}`, { method: 'DELETE', signal }, parseDeletedVideoSceneCutResponse) }
