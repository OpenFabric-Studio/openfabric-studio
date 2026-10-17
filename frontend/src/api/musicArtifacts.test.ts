import { afterEach, expect, it, vi } from 'vitest'
import * as api from './musicArtifacts'
import type { MusicArtifactCleanupPlan, MusicArtifactCleanupReceipt } from './generated'

const jobId = 'a'.repeat(32)
const plan: MusicArtifactCleanupPlan = { id: 'b'.repeat(32), created_at: '2026-10-17T12:00:00Z', plan_sha256: 'c'.repeat(64), items: [{ job_id: jobId, disposition: 'eligible', bytes: 1024 }] }
const receipt: MusicArtifactCleanupReceipt = { plan_id: plan.id, plan_sha256: plan.plan_sha256, status: 'completed', items: [{ job_id: jobId, status: 'removed', removed_bytes: 1024 }] }
afterEach(() => vi.restoreAllMocks())
it('prepares a source-selected plan without sending paths or deleting files', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(plan)))
  const signal = new AbortController().signal
  await expect(api.createMusicCleanupPlan({ analysis_job_ids: [jobId] }, signal)).resolves.toEqual(plan)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music/artifacts/cleanup-plans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ analysis_job_ids: [jobId] }), signal })
})
it('requires explicit confirmation bound to the exact review hash', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(receipt)))
  await expect(api.confirmMusicCleanupPlan(plan.id, { plan_sha256: plan.plan_sha256, confirm_cleanup: true })).resolves.toEqual(receipt)
  expect(fetch).toHaveBeenCalledWith(`/api/music/artifacts/cleanup-plans/${plan.id}/confirm`, expect.objectContaining({ body: JSON.stringify({ plan_sha256: plan.plan_sha256, confirm_cleanup: true }) }))
})
it('rejects unsafe identifiers, duplicate selections and foreign plan or receipt responses', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ ...plan, items: [{ job_id: 'd'.repeat(32), disposition: 'eligible' }] }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...plan, id: 'd'.repeat(32) }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...receipt, plan_sha256: 'd'.repeat(64) })))
  expect(() => api.getMusicCleanupPlan('../outside')).toThrow()
  expect(() => api.createMusicCleanupPlan({ analysis_job_ids: [jobId, jobId] })).toThrow()
  await expect(api.createMusicCleanupPlan({ analysis_job_ids: [jobId] })).rejects.toThrow()
  await expect(api.getMusicCleanupPlan(plan.id)).rejects.toThrow()
  await expect(api.confirmMusicCleanupPlan(plan.id, { plan_sha256: plan.plan_sha256, confirm_cleanup: true })).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(3)
})
it('loads bounded inventory and rejects malformed byte counts', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ generated_at: plan.created_at, groups: [{ id: 'group', kind: 'analysis_workspace', root_alias: 'music_analysis', disposition: 'review', size_bytes: -1 }], scan_complete: true })))
  await expect(api.getMusicArtifacts()).rejects.toThrow()
})
