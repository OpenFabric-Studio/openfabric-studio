import { apiFetch } from './http'
import { parseClipHostStatus, parseClipHostSettingsRequest } from './contracts'
import type { ClipHostStatus, ClipHostSettingsRequest } from './contracts'
const path = '/api/clip-host'
export function getClipHostStatus(signal?: AbortSignal): Promise<ClipHostStatus> { return apiFetch(`${path}/settings`, { signal }, parseClipHostStatus) }
export function saveClipHostSettings(body: ClipHostSettingsRequest, signal?: AbortSignal): Promise<ClipHostStatus> {
  return apiFetch(`${path}/settings`, { method: 'PUT', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseClipHostSettingsRequest(body)) }, parseClipHostStatus)
}
export function checkClipHostConnection(signal?: AbortSignal): Promise<ClipHostStatus> { return apiFetch(`${path}/connection`, { method: 'POST', signal }, parseClipHostStatus) }
export function cleanupClipHostAssets(signal?: AbortSignal): Promise<ClipHostStatus> { return apiFetch(`${path}/cleanup`, { method: 'POST', signal }, parseClipHostStatus) }
