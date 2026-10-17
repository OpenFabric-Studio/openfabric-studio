import { afterEach, expect, it, vi } from 'vitest'
import * as api from './beats'
import { beatDocument, beatJob, beatRequest } from '../views/music/beatTestFixtures'

afterEach(() => vi.restoreAllMocks())
it('saves only the exact document and expected revision', async () => {
  const doc = beatDocument()
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...doc, revision: 1 })))
  const signal = new AbortController().signal
  await expect(api.saveBeat(doc.id, { document: doc, expected_revision: 0 }, signal)).resolves.toMatchObject({ revision: 1 })
  expect(fetch).toHaveBeenCalledExactlyOnceWith(`/api/beats/${doc.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document: doc, expected_revision: 0 }), signal })
  expect(() => api.saveBeat(doc.id, { document: doc, expected_revision: 1 })).toThrow()
})
it('rejects unsafe IDs and cross-beat render history', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ jobs: [beatJob({ beat_id: 'f'.repeat(32) })] })))
  expect(() => api.getBeat('../outside')).toThrow()
  expect(() => api.beatArtifactUrl(beatJob().id, '../outside')).toThrow()
  await expect(api.listBeatJobs(beatDocument().id)).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('binds a render response to the reviewed revision and submission identity', async () => {
  const request = beatRequest()
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(beatJob()))).mockResolvedValueOnce(new Response(JSON.stringify(beatJob({ request: { ...request, submission_key: 'f'.repeat(32) } })))).mockResolvedValueOnce(new Response(JSON.stringify(beatJob({ beat_revision: 1 }))))
  await expect(api.renderBeat(request.beat_id, request)).resolves.toEqual(beatJob())
  await expect(api.renderBeat(request.beat_id, request)).rejects.toThrow()
  await expect(api.renderBeat(request.beat_id, request)).rejects.toThrow()
})
it('rejects foreign single-document and job replies and malformed storage', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ ...beatDocument(), id: 'f'.repeat(32) }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...beatJob(), id: 'f'.repeat(32) }))).mockResolvedValueOnce(new Response(JSON.stringify({ output_bytes: -1, temporary_bytes: 0, required_free_bytes: 0, can_start: true, frames: 1, duration_sec: 1 })))
  await expect(api.getBeat(beatDocument().id)).rejects.toThrow()
  await expect(api.getBeatJob(beatJob().id)).rejects.toThrow()
  await expect(api.previewBeatStorage(beatDocument().id, beatRequest())).rejects.toThrow()
})
it('binds retained revision and editor publication replies to their requested beat', async () => {
  const doc = beatDocument(), provenance = { workflow: 'beat', workflow_id: doc.id, workflow_revision: 0, source_manifest_sha256: 'f'.repeat(64), sources: [] }
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ ...doc, revision: 1 })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ project_id: 17, provenance })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ project_id: 17, provenance: { ...provenance, workflow_revision: 1 } })))
  await expect(api.getBeatRevision(doc.id, 0)).rejects.toThrow()
  await expect(api.publishBeatProject(beatJob().id, doc.id, 0)).resolves.toMatchObject({ project_id: 17 })
  await expect(api.publishBeatProject(beatJob().id, doc.id, 0)).rejects.toThrow()
  expect(() => api.getBeatRevision(doc.id, -1)).toThrow()
})
it('rejects a preset compilation for another preset or sample kit', async () => {
  const request = { preset_id: 'guzheng_dawn', kit_id: 'test-kit', document_id: 'a'.repeat(32), seed: 42, tonic_midi: 60 }
  const result = { preset_id: request.preset_id, preset_sha256: 'b'.repeat(64), catalog_version: 'report-v1-provisional', warnings: ['candidate_arrangement_unreviewed'], document: beatDocument() }
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(result)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...result, preset_id: 'other' })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...result, document: { ...result.document, kit_id: 'other' } })))
  await expect(api.compileBeatPreset(request)).resolves.toMatchObject({ preset_id: request.preset_id })
  await expect(api.compileBeatPreset(request)).rejects.toThrow()
  await expect(api.compileBeatPreset(request)).rejects.toThrow()
})
