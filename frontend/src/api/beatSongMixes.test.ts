import { afterEach, expect, it, vi } from 'vitest'
import previewFixture from './beatSongMixPreviewFixture.json'
import jobFixture from './beatSongMixJobFixture.json'
import { parseBeatSongMixJob, parseBeatSongMixPreview } from './generated'
import * as api from './beatSongMixes'

const preview = () => parseBeatSongMixPreview(structuredClone(previewFixture))
const job = () => parseBeatSongMixJob(structuredClone(jobFixture))
afterEach(() => vi.unstubAllGlobals())
function response(value: unknown) { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(value)))) }

it('refuses path traversal and newline identities before network access', () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  expect(() => api.getBeatSongPlan('../private')).toThrow()
  expect(() => api.getBeatSongPlan(`${preview().plan.id}\n`)).toThrow()
  expect(() => api.beatSongArtifactUrl(job().id, '../file')).toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})
it('parses real source metadata and refuses a different retained source', async () => {
  const plan = preview().plan
  response(plan.song)
  await expect(api.inspectBeatSongSource({ source: plan.beat.source })).rejects.toThrow()
  response(plan.beat)
  expect((await api.inspectBeatSongSource({ source: plan.beat.source })).sha256).toBe(plan.beat.sha256)
})
it('binds a render response to the reviewed plan, revision, hash and submission', async () => {
  const result = job()
  response(result)
  expect((await api.startBeatSongMix(result.request)).id).toBe(result.id)
  response({ ...result, request: { ...result.request, submission_key: 'foreign' } })
  await expect(api.startBeatSongMix(result.request)).rejects.toThrow()
  response({ ...result, manifest: { ...result.manifest, plan_sha256: 'a'.repeat(64) } })
  await expect(api.getBeatSongJob(result.id)).rejects.toThrow()
})
it('uses CAS for plan edits and rejects a foreign or stale plan response', async () => {
  const { plan } = preview()
  response({ ...preview(), plan: { ...plan, revision: 1 } })
  await api.updateBeatSongPlan(plan.id, { expected_revision: 0, draft: plan.draft })
  expect(fetch).toHaveBeenCalledWith(`/api/beat-song-mixes/plans/${plan.id}`, expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ expected_revision: 0, draft: plan.draft }) }))
  response(preview())
  await expect(api.updateBeatSongPlan(plan.id, { expected_revision: 0, draft: plan.draft })).rejects.toThrow()
})
it('keeps save retry separate from rendering and verifies editor ownership', async () => {
  const result = job()
  response(result)
  await api.retryBeatSongSave(result.id)
  expect(fetch).toHaveBeenCalledWith(`/api/beat-song-mixes/${result.id}/retry-save`, expect.objectContaining({ method: 'POST', body: '{}' }))
  response({ project_id: 2, provenance: { workflow: 'mashup', workflow_id: result.plan.id, workflow_revision: 0, source_manifest_sha256: result.plan.sha256 } })
  await expect(api.publishBeatSongProject(result)).rejects.toThrow()
})
it('rejects inconsistent source hashes, lane identities and artifact ownership', async () => {
  const original = preview()
  response({ ...original.plan, beat: { ...original.plan.beat, sha256: 'a'.repeat(64) } })
  await expect(api.getBeatSongPlan(original.plan.id)).rejects.toThrow()
  const result = job(), lane = original.plan.lanes[0]
  if (!lane || !result.manifest) throw new Error('Missing real fixture lanes')
  response({ ...original.plan, lanes: [{ ...lane, control: { ...lane.control, lane_id: 'c'.repeat(32) } }, ...original.plan.lanes.slice(1)] })
  await expect(api.getBeatSongPlan(original.plan.id)).rejects.toThrow()
  response({ ...result, manifest: { ...result.manifest, sources: result.manifest.sources.map(source => ({ ...source, sha256: 'a'.repeat(64) })) } })
  await expect(api.getBeatSongJob(result.id)).rejects.toThrow()
})
