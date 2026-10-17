import { apiFetch } from './http'
import { parseConfirmMusicArtifactCleanup, parseMusicArtifactCleanupPlan, parseMusicArtifactCleanupReceipt, parseMusicArtifactCleanupRequest, parseMusicArtifactInventory } from './generated'
import type { ConfirmMusicArtifactCleanup, MusicArtifactCleanupRequest } from './generated'

const base = '/api/music/artifacts'
function planPath(id: string) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid cleanup identifier')
  return `${base}/cleanup-plans/${id}`
}
export const getMusicArtifacts = (signal?: AbortSignal) => apiFetch(base, { signal }, parseMusicArtifactInventory)
export function createMusicCleanupPlan(body: MusicArtifactCleanupRequest, signal?: AbortSignal) {
  const request = parseMusicArtifactCleanupRequest(body)
  if (new Set(request.analysis_job_ids).size !== request.analysis_job_ids.length) throw new TypeError('Duplicate cleanup selection')
  return apiFetch(`${base}/cleanup-plans`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal }, value => {
    const plan = parseMusicArtifactCleanupPlan(value)
    if (plan.items.length !== request.analysis_job_ids.length || plan.items.some(item => !request.analysis_job_ids.includes(item.job_id))
        || new Set(plan.items.map(item => item.job_id)).size !== plan.items.length) throw new TypeError('Mismatched cleanup selection')
    return plan
  })
}
export const getMusicCleanupPlan = (id: string, signal?: AbortSignal) => apiFetch(planPath(id), { signal }, value => {
  const plan = parseMusicArtifactCleanupPlan(value)
  if (plan.id !== id) throw new TypeError('Mismatched cleanup plan')
  return plan
})
export const getMusicCleanupReceipt = (id: string, signal?: AbortSignal) => apiFetch(`${planPath(id)}/receipt`, { signal }, value => {
  const receipt = parseMusicArtifactCleanupReceipt(value)
  if (receipt.plan_id !== id) throw new TypeError('Mismatched cleanup receipt')
  return receipt
})
export function confirmMusicCleanupPlan(id: string, body: ConfirmMusicArtifactCleanup, signal?: AbortSignal) {
  const request = parseConfirmMusicArtifactCleanup(body)
  return apiFetch(`${planPath(id)}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal }, value => {
    const receipt = parseMusicArtifactCleanupReceipt(value)
    if (receipt.plan_id !== id || receipt.plan_sha256 !== request.plan_sha256) throw new TypeError('Mismatched cleanup receipt')
    return receipt
  })
}
