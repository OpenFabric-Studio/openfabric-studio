// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import * as api from '../../api/videos'
import * as tracks from '../../api/tracks'
import { ApiError } from '../../api/http'
import { parseVideoProject, type VideoProject, type VideoShotDraft } from '../../api/contracts'
import { useVideoWorkspace } from './useVideoWorkspace'
import { videoProjectFixture, videoTrack, videoReadinessFixture } from './videoFixtures'

vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn() }))
vi.mock('../../api/videos', async (original) => ({ ...await original<typeof import('../../api/videos')>(), listVideoProjects: vi.fn(), getVideoProject: vi.fn(), listVideos: vi.fn(), otherWorkBusy: vi.fn(), videoReadiness: vi.fn(), createVideoProject: vi.fn(), updateVideoProject: vi.fn(), analyzeVideoProject: vi.fn(), previewVideoProject: vi.fn(), renderVideoProject: vi.fn(), cancelVideoProject: vi.fn(), duplicateVideoProject: vi.fn(), uploadVideoReference: vi.fn(), approveVideoVariant: vi.fn(), resumeVideoProject: vi.fn(), exportVideoProject: vi.fn(), deleteVideoProject: vi.fn() }))
let app: App | undefined
let project: VideoProject
beforeEach(() => {
  vi.useFakeTimers()
  project = videoProjectFixture()
  vi.mocked(tracks.listTracks).mockResolvedValue([videoTrack])
  vi.mocked(api.listVideos).mockResolvedValue({ videos: [] })
  vi.mocked(api.otherWorkBusy).mockResolvedValue(false)
  vi.mocked(api.videoReadiness).mockResolvedValue(videoReadinessFixture)
  vi.mocked(api.listVideoProjects).mockImplementation(async () => ({ projects: [project] }))
  vi.mocked(api.getVideoProject).mockImplementation(async () => project)
  vi.mocked(api.updateVideoProject).mockImplementation(async (_id, body) => { project = parseVideoProject({ ...project, ...body, revision: project.revision + 1, shots: body.shots ?? project.shots }); return project })
  vi.mocked(api.cancelVideoProject).mockImplementation(async () => ({ ...project, revision: project.revision + 1, job: project.job ? { ...project.job, status: 'cancelled' } : null }))
  vi.mocked(api.deleteVideoProject).mockResolvedValue(undefined)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); sessionStorage.clear(); vi.clearAllMocks(); vi.useRealTimers() })
async function flush() { for (let i = 0; i < 12; i++) await nextTick() }
async function mount() {
  let workspace: ReturnType<typeof useVideoWorkspace> | undefined
  app = createApp({ setup() { workspace = useVideoWorkspace(); return () => h('div') } })
  app.mount(document.body.appendChild(document.createElement('div')))
  await flush()
  if (!workspace) throw new Error('Workspace did not mount')
  return workspace
}
function currentDraft(workspace: ReturnType<typeof useVideoWorkspace>) {
  if (!workspace.draft.value) throw new Error('Missing draft')
  return workspace.draft.value
}
it('cancels an active job without trying to save or discarding a recovered draft', async () => {
  project.job = { id: 'd'.repeat(32), status: 'running', operation: 'preview' }
  sessionStorage.setItem(`openfabric:video-draft:${project.id}`, JSON.stringify({ revision: 1, direction: 'Keep my recovered edit', shots: project.shots?.map(({ variants: _variants, approved_variant_id: _approval, ...shot }) => shot) }))
  const workspace = await mount()
  expect(workspace.dirty.value).toBe(true)
  await workspace.cancel()
  expect(api.cancelVideoProject).toHaveBeenCalledWith(project.id, expect.any(AbortSignal))
  expect(api.updateVideoProject).not.toHaveBeenCalled()
  expect(workspace.project.value?.job?.status).toBe('cancelled')
  expect(currentDraft(workspace).direction).toBe('Keep my recovered edit')
  expect(workspace.dirty.value).toBe(true)
  expect(JSON.parse(sessionStorage.getItem(`openfabric:video-draft:${project.id}`) || '{}')).toMatchObject({ revision: 2, direction: 'Keep my recovered edit' })
})
it('selects an existing shot after undo removes the selected newly added shot', async () => {
  const workspace = await mount()
  workspace.addShot()
  expect(currentDraft(workspace).shots.some((shot) => shot.id === workspace.selectedShotId.value)).toBe(true)
  workspace.undo()
  expect(workspace.selectedShot.value?.id).toBe('b'.repeat(32))
})
it('blocks undo and new shot selection while a job is active', async () => {
  const workspace = await mount()
  workspace.addShot()
  const before: VideoShotDraft[] = currentDraft(workspace).shots.map((shot) => ({ ...shot }))
  const selected = workspace.selectedShotId.value
  workspace.project.value = { ...project, job: { id: 'd'.repeat(32), status: 'running', operation: 'preview' } }
  workspace.undo()
  workspace.addShot()
  expect(currentDraft(workspace).shots).toEqual(before)
  expect(workspace.selectedShotId.value).toBe(selected)
})
it('owns project selection until its response arrives and blocks old-project edits', async () => {
  const workspace = await mount()
  const second = videoProjectFixture('e'.repeat(32))
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.getVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = workspace.selectProject(second.id)
  await flush()
  expect(workspace.readOnly.value).toBe(true)
  workspace.addShot()
  expect(currentDraft(workspace).shots).toHaveLength(2)
  release(second)
  await pending
  expect(workspace.project.value?.id).toBe(second.id)
  expect(workspace.readOnly.value).toBe(false)
})
it('repairs selected shot and checked preview identities after a re-analysis replaces unlocked shots', async () => {
  const workspace = await mount()
  const replacement: VideoProject = { ...project, revision: 2, shots: [{ id: 'f'.repeat(32), start_sec: 0, seconds: 4, prompt: 'New suggested scene' }] }
  vi.mocked(api.analyzeVideoProject).mockResolvedValue(replacement)
  await workspace.analyze()
  expect(workspace.selectedShot.value?.id).toBe('f'.repeat(32))
  expect(workspace.selectedPreviewIds.value).toEqual(['f'.repeat(32)])
  expect(workspace.step.value).toBe('storyboard')
})
it('saves the full selected direction, shot, timing and export settings before rendering', async () => {
  const workspace = await mount()
  const draft = currentDraft(workspace)
  draft.name = 'Reviewed video'
  draft.direction = 'A recurring subject in warm light'
  draft.seed = 42
  draft.settings = { engine_pack: 'ltx25', width: 1280, height: 704, stage1_steps: 50, stage2_steps: 1, cfg_scale: 4.5, negative_prompt: 'blur' }
  draft.export_settings = { aspect: 'square', quality: 'high', include_overlays: true }
  const first = draft.shots[0]
  if (!first) throw new Error('Missing shot')
  first.seed = 123
  first.reference_id = 'f'.repeat(32)
  first.reference_strength = .25
  first.locked = true
  draft.markers = [{ id: '1'.repeat(32), kind: 'manual', time_sec: 4, label: 'Drop', confidence: 1 }]
  draft.overlays = [{ id: '2'.repeat(32), kind: 'lyric', text: 'Reviewed timed line', start_sec: 1, end_sec: 3, position: 'top', font_size: 48, color: '#ff0000' }]
  vi.mocked(api.renderVideoProject).mockImplementation(async () => project)
  await workspace.render()
  expect(api.updateVideoProject).toHaveBeenCalledWith(project.id, expect.objectContaining({ revision: 1, name: draft.name, direction: draft.direction, seed: 42, settings: draft.settings, export_settings: draft.export_settings, markers: draft.markers, overlays: draft.overlays, shots: expect.arrayContaining([expect.objectContaining({ seed: 123, reference_id: 'f'.repeat(32), reference_strength: .25, locked: true })]) }), expect.any(AbortSignal))
  expect(api.renderVideoProject).toHaveBeenCalledWith(project.id, { revision: 2, reuse_completed: true }, expect.any(AbortSignal))
  expect(workspace.step.value).toBe('export')
  expect(workspace.dirty.value).toBe(false)
})
it('keeps unsaved changes and blocks analysis when the save fails', async () => {
  const workspace = await mount()
  currentDraft(workspace).direction = 'Recover this scene'
  vi.mocked(api.updateVideoProject).mockRejectedValue(new Error('fixture save failed'))
  await workspace.analyze()
  expect(api.analyzeVideoProject).not.toHaveBeenCalled()
  expect(workspace.dirty.value).toBe(true)
  expect(currentDraft(workspace).direction).toBe('Recover this scene')
  expect(workspace.saveError.value).toBe('unknown')
  expect(workspace.acting.value).toBe(false)
})
it('discards a malformed browser draft while retaining the saved storyboard', async () => {
  sessionStorage.setItem(`openfabric:video-draft:${project.id}`, JSON.stringify({ revision: 1, seed: 'not a number', shots: [] }))
  const workspace = await mount()
  expect(workspace.dirty.value).toBe(false)
  expect(currentDraft(workspace).shots).toHaveLength(2)
})
it('recovers a partial browser draft with saved shots and explicit backend defaults', async () => {
  sessionStorage.setItem(`openfabric:video-draft:${project.id}`, JSON.stringify({ revision: 1, name: 'Recovered name', settings: { cfg_scale: 4 }, export_settings: { quality: 'high' } }))
  const workspace = await mount()
  expect(workspace.dirty.value).toBe(true)
  expect(currentDraft(workspace).name).toBe('Recovered name')
  expect(currentDraft(workspace).shots).toHaveLength(2)
  expect(currentDraft(workspace).settings).toMatchObject({ engine_pack: 'ltx23', width: 704, height: 448, stage1_steps: 30, stage2_steps: 3, cfg_scale: 4 })
  expect(currentDraft(workspace).export_settings).toEqual({ aspect: 'landscape', quality: 'high', include_overlays: true, attach_speech: false })
})
it('selects a recoverable draft shot when the first saved shot was removed locally', async () => {
  const second = project.shots?.[1]
  if (!second) throw new Error('Missing saved shot')
  const { variants: _variants, approved_variant_id: _approval, ...shot } = second
  sessionStorage.setItem(`openfabric:video-draft:${project.id}`, JSON.stringify({ revision: 1, shots: [shot] }))
  const workspace = await mount()
  expect(workspace.selectedShot.value?.id).toBe(second.id)
  expect(workspace.selectedPreviewIds.value).toEqual([second.id])
})
it('does not activate a completed duplicate response after teardown', async () => {
  const workspace = await mount()
  const duplicate = videoProjectFixture('e'.repeat(32))
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.duplicateVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = workspace.duplicate()
  await flush()
  app?.unmount(); app = undefined
  release(duplicate)
  await pending
  expect(workspace.project.value?.id).toBe(project.id)
  expect(vi.getTimerCount()).toBe(0)
})
it('uploads only supported reference files and preserves the reviewed revision', async () => {
  const workspace = await mount()
  await workspace.upload(new File(['text'], 'lyrics.txt', { type: 'text/plain' }))
  expect(api.uploadVideoReference).not.toHaveBeenCalled()
  expect(workspace.error.value).toBe('invalid_reference')
  vi.mocked(api.uploadVideoReference).mockResolvedValue({ ...project, revision: 2 })
  const image = new File(['fixture image'], 'cover.png', { type: 'image/png' })
  await workspace.upload(image)
  expect(api.uploadVideoReference).toHaveBeenCalledWith(project.id, 1, image, expect.any(AbortSignal))
  expect(workspace.project.value?.revision).toBe(2)
})

it('removes the selected project and its browser draft while retaining the source song', async () => {
  const workspace = await mount()
  workspace.step.value = 'storyboard'
  workspace.addShot()
  expect(sessionStorage.getItem(`openfabric:video-draft:${project.id}`)).not.toBeNull()
  expect(await workspace.removeProject(project.id)).toBe(true)
  expect(api.deleteVideoProject).toHaveBeenCalledWith(project.id, expect.any(AbortSignal))
  expect(workspace.projects.value).toEqual([])
  expect(workspace.project.value).toBeNull()
  expect(workspace.draft.value).toBeNull()
  expect(workspace.step.value).toBe('song')
  expect(workspace.selectedShotId.value).toBe('')
  expect(workspace.selectedPreviewIds.value).toEqual([])
  expect(workspace.undoStack.value).toEqual([])
  expect(workspace.dirty.value).toBe(false)
  expect(sessionStorage.getItem(`openfabric:video-draft:${project.id}`)).toBeNull()
  expect(workspace.tracks.value).toEqual([videoTrack])
  await vi.advanceTimersByTimeAsync(600)
  expect(api.updateVideoProject).not.toHaveBeenCalled()
})
it('removes a different project without saving or replacing the selected dirty draft', async () => {
  const second = videoProjectFixture('e'.repeat(32))
  vi.mocked(api.listVideoProjects).mockResolvedValueOnce({ projects: [project, second] })
  sessionStorage.setItem(`openfabric:video-draft:${second.id}`, JSON.stringify({ revision: 1, name: 'Other recovered draft' }))
  const workspace = await mount()
  currentDraft(workspace).direction = 'Keep the selected edit'
  workspace.step.value = 'preview'
  expect(await workspace.removeProject(second.id)).toBe(true)
  expect(workspace.projects.value.map((row) => row.id)).toEqual([project.id])
  expect(workspace.project.value?.id).toBe(project.id)
  expect(currentDraft(workspace).direction).toBe('Keep the selected edit')
  expect(workspace.dirty.value).toBe(true)
  expect(workspace.step.value).toBe('preview')
  expect(api.updateVideoProject).not.toHaveBeenCalled()
  expect(sessionStorage.getItem(`openfabric:video-draft:${second.id}`)).toBeNull()
  expect(sessionStorage.getItem(`openfabric:video-draft:${project.id}`)).not.toBeNull()
})
it('retains the selected project and recovered edits when deletion fails', async () => {
  const workspace = await mount()
  currentDraft(workspace).direction = 'Recover this edit'
  vi.mocked(api.deleteVideoProject).mockRejectedValueOnce(new ApiError('storage_failed', 500))
  expect(await workspace.removeProject(project.id)).toBe(false)
  expect(workspace.project.value?.id).toBe(project.id)
  expect(workspace.projects.value).toHaveLength(1)
  expect(currentDraft(workspace).direction).toBe('Recover this edit')
  expect(workspace.dirty.value).toBe(true)
  expect(workspace.error.value).toBe('storage_failed')
  expect(workspace.readOnly.value).toBe(false)
  expect(sessionStorage.getItem(`openfabric:video-draft:${project.id}`)).not.toBeNull()
  expect(await workspace.save()).toBe(true)
})
it('blocks deletion of a queued or running project and unknown identities', async () => {
  const workspace = await mount()
  for (const status of ['queued', 'running'] as const) {
    const busy: VideoProject = { ...project, job: { id: 'd'.repeat(32), status, operation: 'render' } }
    workspace.project.value = busy
    workspace.projects.value = [busy]
    expect(await workspace.removeProject(project.id)).toBe(false)
  }
  expect(await workspace.removeProject('f'.repeat(32))).toBe(false)
  expect(api.deleteVideoProject).not.toHaveBeenCalled()
})
it('owns deletion until it settles and rejects concurrent project actions', async () => {
  const workspace = await mount()
  let release: () => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.deleteVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = workspace.removeProject(project.id)
  await flush()
  expect(workspace.readOnly.value).toBe(true)
  await workspace.selectProject('e'.repeat(32))
  await workspace.removeProject(project.id)
  workspace.addShot()
  expect(api.getVideoProject).not.toHaveBeenCalled()
  expect(api.deleteVideoProject).toHaveBeenCalledTimes(1)
  expect(currentDraft(workspace).shots).toHaveLength(2)
  release()
  expect(await pending).toBe(true)
  expect(workspace.acting.value).toBe(false)
})
it('waits for an in-flight autosave before issuing delete', async () => {
  const workspace = await mount()
  currentDraft(workspace).direction = 'Saving while delete is requested'
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.updateVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pendingSave = workspace.save()
  const pendingDelete = workspace.removeProject(project.id)
  await flush()
  expect(api.deleteVideoProject).not.toHaveBeenCalled()
  release({ ...project, revision: 2 })
  await pendingSave
  expect(await pendingDelete).toBe(true)
  expect(workspace.project.value).toBeNull()
  expect(workspace.projects.value).toEqual([])
})
it('does not let a poll started before deletion restore the removed project', async () => {
  const workspace = await mount()
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.getVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  await vi.advanceTimersByTimeAsync(2000)
  expect(await workspace.removeProject(project.id)).toBe(true)
  release(project)
  await flush()
  expect(workspace.project.value).toBeNull()
  expect(workspace.projects.value).toEqual([])
})
it('ignores a deleted-project poll failure that settles after removal', async () => {
  const workspace = await mount()
  let reject: (cause: Error) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.getVideoProject).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail }))
  await vi.advanceTimersByTimeAsync(2000)
  expect(await workspace.removeProject(project.id)).toBe(true)
  reject(new Error('not_found'))
  await flush()
  expect(workspace.error.value).toBe('')
})
it('invalidates polls that start while deletion is pending', async () => {
  const workspace = await mount()
  let releaseDelete: () => void = () => { throw new Error('Not initialized') }
  let releasePoll: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.deleteVideoProject).mockReturnValueOnce(new Promise((resolve) => { releaseDelete = resolve }))
  vi.mocked(api.getVideoProject).mockReturnValueOnce(new Promise((resolve) => { releasePoll = resolve }))
  const pending = workspace.removeProject(project.id)
  await vi.advanceTimersByTimeAsync(2000)
  releaseDelete()
  expect(await pending).toBe(true)
  releasePoll(project)
  await flush()
  expect(workspace.projects.value).toEqual([])
  expect(workspace.project.value).toBeNull()
})
it('aborts deletion on teardown and ignores a late successful response', async () => {
  const workspace = await mount()
  let release: () => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.deleteVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = workspace.removeProject(project.id)
  await flush()
  const signal = vi.mocked(api.deleteVideoProject).mock.calls[0]?.[1]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  release()
  expect(await pending).toBe(false)
  expect(workspace.projects.value).toHaveLength(1)
  expect(workspace.project.value?.id).toBe(project.id)
  expect(vi.getTimerCount()).toBe(0)
})
it('keeps the accepted revision when removing another project during an in-flight save', async () => {
  const second = videoProjectFixture('e'.repeat(32))
  vi.mocked(api.listVideoProjects).mockResolvedValueOnce({ projects: [project, second] })
  const workspace = await mount()
  currentDraft(workspace).direction = 'Persist selected project'
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.updateVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pendingSave = workspace.save()
  const pendingDelete = workspace.removeProject(second.id)
  await flush()
  const saved: VideoProject = { ...project, revision: 2, direction: 'Persist selected project' }
  release(saved)
  expect(await pendingSave).toBe(true)
  expect(await pendingDelete).toBe(true)
  expect(workspace.project.value?.revision).toBe(2)
  expect(currentDraft(workspace).direction).toBe(saved.direction)
  expect(workspace.dirty.value).toBe(false)
})
it('pauses pending autosave during removal of another project, then saves the retained draft', async () => {
  const second = videoProjectFixture('e'.repeat(32))
  vi.mocked(api.listVideoProjects).mockResolvedValueOnce({ projects: [project, second] })
  const workspace = await mount()
  currentDraft(workspace).direction = 'Save after removal finishes'
  let release: () => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.deleteVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = workspace.removeProject(second.id)
  await vi.advanceTimersByTimeAsync(600)
  expect(api.updateVideoProject).not.toHaveBeenCalled()
  expect(await workspace.save()).toBe(false)
  release()
  expect(await pending).toBe(true)
  await vi.advanceTimersByTimeAsync(600)
  expect(api.updateVideoProject).toHaveBeenCalledTimes(1)
  expect(workspace.project.value?.revision).toBe(2)
  expect(workspace.dirty.value).toBe(false)
})
it('resumes pending autosave when selected-project deletion fails', async () => {
  const workspace = await mount()
  currentDraft(workspace).direction = 'Recover and save automatically'
  vi.mocked(api.deleteVideoProject).mockRejectedValueOnce(new ApiError('storage_failed', 500))
  expect(await workspace.removeProject(project.id)).toBe(false)
  await vi.advanceTimersByTimeAsync(600)
  expect(api.updateVideoProject).toHaveBeenCalledTimes(1)
  expect(workspace.project.value?.revision).toBe(2)
  expect(currentDraft(workspace).direction).toBe('Recover and save automatically')
})
it('retains a failed deletion error after an older in-flight poll also fails', async () => {
  const workspace = await mount()
  let rejectDelete: (cause: Error) => void = () => { throw new Error('Not initialized') }
  let rejectPoll: (cause: Error) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.deleteVideoProject).mockReturnValueOnce(new Promise((_resolve, reject) => { rejectDelete = reject }))
  vi.mocked(api.getVideoProject).mockReturnValueOnce(new Promise((_resolve, reject) => { rejectPoll = reject }))
  const pending = workspace.removeProject(project.id)
  await vi.advanceTimersByTimeAsync(2000)
  rejectDelete(new ApiError('storage_failed', 500))
  expect(await pending).toBe(false)
  const error = workspace.error.value
  rejectPoll(new ApiError('not_found', 404))
  await flush()
  expect(workspace.error.value).toBe(error)
  expect(workspace.projects.value).toHaveLength(1)
})
it('does not expose unexpected or unknown server messages after failed deletion', async () => {
  const workspace = await mount()
  for (const cause of [new Error('/private/project/path'), new ApiError('/private/project/path', 500)]) {
    vi.mocked(api.deleteVideoProject).mockRejectedValueOnce(cause)
    expect(await workspace.removeProject(project.id)).toBe(false)
    expect(workspace.error.value).toBe('project_delete_failed')
    expect(workspace.projects.value).toHaveLength(1)
  }
})
