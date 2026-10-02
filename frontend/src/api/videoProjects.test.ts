// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { analyzeVideoProject, approveVideoVariant, cancelVideoProject, createVideoProject, deleteVideoProject, duplicateVideoProject, exportVideoProject, getVideoProject, listVideoProjects, previewVideoProject, renderVideoProject, resumeVideoProject, updateVideoProject, uploadVideoReference, videoReadiness } from './videos'
import { videoReadinessFixture } from '../views/video/videoFixtures'

const project = { id: 'a'.repeat(32), revision: 2, track_id: 1, track_title: 'Song', name: 'Video', duration_sec: 20,
  source_fingerprint: 'source', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' }
afterEach(() => { vi.unstubAllGlobals() })
function response() { return new Response(JSON.stringify(project), { headers: { 'Content-Type': 'application/json' } }) }

describe('video project API boundaries', () => {
  it('sends revision and only the selected preview shot identities', async () => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    await previewVideoProject(project.id, { revision: 1, shot_ids: ['b'.repeat(32)], variants_per_shot: 2 })
    expect(fetch).toHaveBeenCalledWith(`/api/videos/projects/${project.id}/preview`, expect.objectContaining({ body: JSON.stringify({ revision: 1, shot_ids: ['b'.repeat(32)], variants_per_shot: 2 }) }))
  })
  it('rejects an invalid project response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...project, revision: 0 }))))
    await expect(createVideoProject({ track_id: 1, name: 'Video' })).rejects.toThrow()
  })
  it('validates request limits before performing a save', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(updateVideoProject(project.id, { revision: 0 })).rejects.toThrow()
    expect(fetch).not.toHaveBeenCalled()
  })
  it('approval addresses the project and shot with the reviewed revision', async () => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    await approveVideoVariant(project.id, 'b'.repeat(32), { revision: 1, variant_id: 'c'.repeat(32) })
    expect(fetch).toHaveBeenCalledWith(`/api/videos/projects/${project.id}/shots/${'b'.repeat(32)}/approve`, expect.objectContaining({ method: 'POST' }))
  })
  it.each([
    ['analyze', analyzeVideoProject], ['duplicate', duplicateVideoProject], ['resume', resumeVideoProject],
  ])('sends the reviewed revision and owned abort signal to %s', async (action, request) => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()
    await request(project.id, { revision: 2 }, controller.signal)
    expect(fetch).toHaveBeenCalledWith(`/api/videos/projects/${project.id}/${action}`, expect.objectContaining({ method: 'POST', body: JSON.stringify({ revision: 2 }), signal: controller.signal }))
  })
  it('renders missing shots with the current revision and explicit reuse behavior', async () => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    await renderVideoProject(project.id, { revision: 2, reuse_completed: true })
    expect(fetch).toHaveBeenCalledWith(`/api/videos/projects/${project.id}/render`, expect.objectContaining({ body: JSON.stringify({ revision: 2, reuse_completed: true }) }))
  })
  it('sends every export option without silently applying defaults over the selection', async () => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    await exportVideoProject(project.id, { revision: 2, settings: { aspect: 'portrait', quality: 'high', include_overlays: false } })
    expect(fetch).toHaveBeenCalledWith(`/api/videos/projects/${project.id}/export`, expect.objectContaining({ body: JSON.stringify({ revision: 2, settings: { aspect: 'portrait', quality: 'high', include_overlays: false } }) }))
  })
  it('cancels without a stale revision requirement', async () => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    await cancelVideoProject(project.id)
    expect(fetch).toHaveBeenCalledWith(`/api/videos/projects/${project.id}/cancel`, expect.objectContaining({ body: '{}' }))
  })
  it('sends uploaded reference bytes and revision using multipart data', async () => {
    const fetch = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetch)
    const file = new File(['fixture image'], 'cover.png', { type: 'image/png' })
    await uploadVideoReference(project.id, 2, file)
    const options: unknown = fetch.mock.calls[0]?.[1]
    expect(options).toMatchObject({ method: 'POST' })
    if (typeof options !== 'object' || options === null || !('body' in options) || !(options.body instanceof FormData)) throw new Error('Missing multipart upload')
    expect(options.body.get('revision')).toBe('2')
    expect(options.body.get('file')).toBeInstanceOf(File)
  })
  it('loads project and readiness inventories through their generated response validators', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response())
      .mockResolvedValueOnce(new Response(JSON.stringify({ projects: [project] })))
      .mockResolvedValueOnce(new Response(JSON.stringify(videoReadinessFixture)))
    vi.stubGlobal('fetch', fetch)
    expect(await getVideoProject(project.id)).toEqual(project)
    expect(await listVideoProjects()).toEqual({ projects: [project] })
    expect(await videoReadiness()).toEqual(videoReadinessFixture)
  })
  it('deletes only the addressed saved project', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ deleted: project.id })))
    vi.stubGlobal('fetch', fetch)
    await deleteVideoProject(project.id)
    expect(fetch).toHaveBeenCalledWith(`/api/videos/projects/${project.id}`, expect.objectContaining({ method: 'DELETE' }))
  })
})
