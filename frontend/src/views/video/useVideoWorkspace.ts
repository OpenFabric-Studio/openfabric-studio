import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { listTracks, type SavedTrack } from '../../api/tracks'
import * as api from '../../api/videos'
import { ApiError } from '../../api/http'
import { i18n } from '../../i18n'
import { parseUpdateVideoProjectRequest, type VideoReadinessResponse, type UpdateVideoProjectRequest, type CreateVideoProjectRequest, type VideoProject, type VideoProjectSettings, type VideoExportSettings, type VideoMarker, type VideoOverlay, type VideoShotDraft } from '../../api/contracts'
import { createPollingLoop } from '../../composables/polling'
import { newVideoId, toShotDraft, shotProblem, frameTime, type VideoWorkspaceStep } from './videoWorkspace'

export interface VideoDraft {
  name: string
  mode: NonNullable<VideoProject['mode']>
  direction: string
  seed: number
  settings: VideoProjectSettings
  export_settings: VideoExportSettings
  shots: VideoShotDraft[]
  markers: VideoMarker[]
  overlays: VideoOverlay[]
}

function fromProject(project: VideoProject): VideoDraft {
  return { name: project.name, mode: project.mode ?? 'generated', direction: project.direction ?? '', seed: project.seed ?? 42,
    settings: { engine_pack: 'ltx23', width: 704, height: 448, stage1_steps: 30, stage2_steps: 3, cfg_scale: 3, ...project.settings },
    export_settings: { aspect: 'landscape', quality: 'standard', include_overlays: true, ...project.export_settings },
    shots: (project.shots ?? []).map(toShotDraft), markers: (project.markers ?? []).map((item) => ({ ...item })), overlays: (project.overlays ?? []).map((item) => ({ ...item })) }
}
function snapshot(draft: VideoDraft, revision: number): UpdateVideoProjectRequest {
  return { revision, name: draft.name, mode: draft.mode, direction: draft.direction, seed: draft.seed, settings: { ...draft.settings }, export_settings: { ...draft.export_settings },
    shots: draft.shots.map((shot) => ({ ...shot })), markers: draft.markers.map((marker) => ({ ...marker })), overlays: draft.overlays.map((overlay) => ({ ...overlay })) }
}
const draftKey = (id: string) => `openfabric:video-draft:${id}`
function restoreDraft(project: VideoProject): VideoDraft | null {
  try {
    const raw = sessionStorage.getItem(draftKey(project.id))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    const body = parseUpdateVideoProjectRequest(parsed)
    if (body.revision !== project.revision) return null
    const saved = fromProject(project)
    return { name: body.name ?? saved.name, mode: body.mode ?? saved.mode,
      direction: body.direction ?? saved.direction, seed: body.seed ?? saved.seed,
      settings: { ...saved.settings, ...body.settings }, export_settings: { ...saved.export_settings, ...body.export_settings },
      shots: body.shots ?? saved.shots, markers: body.markers ?? saved.markers, overlays: body.overlays ?? saved.overlays }
  } catch { return null }
}

export function useVideoWorkspace() {
  const tracks = ref<SavedTrack[]>([])
  const projects = ref<VideoProject[]>([])
  const legacyVideos = ref<api.VideoJob[]>([])
  const project = ref<VideoProject | null>(null)
  const draft = ref<VideoDraft | null>(null)
  const step = ref<VideoWorkspaceStep>('song')
  const selectedShotId = ref('')
  const selectedPreviewIds = ref<string[]>([])
  const variantsPerShot = ref(1)
  const trackId = ref<number | null>(null)
  const loading = ref(false)
  const acting = ref(false)
  const saving = ref(false)
  const dirty = ref(false)
  const error = ref('')
  const saveError = ref('')
  const serverBusy = ref(false)
  const readiness = ref<VideoReadinessResponse | null>(null)
  const now = ref(Date.now())
  const undoStack = ref<VideoShotDraft[][]>([])
  const selectedTrack = computed(() => tracks.value.find((item) => item.id === (project.value?.track_id ?? trackId.value)))
  const selectedShot = computed(() => draft.value?.shots.find((item) => item.id === selectedShotId.value))
  const savedShot = computed(() => project.value?.shots?.find((item) => item.id === selectedShotId.value))
  const active = computed(() => api.isVideoActive(project.value?.job?.status))
  const readOnly = computed(() => acting.value || active.value)
  const problem = computed(() => draft.value?.shots.map((shot) => shotProblem(draft.value?.shots ?? [], shot.id, project.value?.preset === 'reel' ? 15 : (project.value?.duration_sec ?? 0))).find(Boolean) ?? '')
  const coverageEnd = computed(() => Math.max(0, ...(draft.value?.shots ?? []).map((shot) => shot.start_sec + (shot.seconds ?? 4))))
  const approvalCount = computed(() => project.value?.shots?.filter((shot) => shot.approved_variant_id).length ?? 0)
  let alive = true
  let generation = 0
  let suppressWatch = false
  let editVersion = 0
  let acceptedVersion = 0
  let saveTimer: ReturnType<typeof setTimeout> | undefined
  let clock: ReturnType<typeof setInterval> | undefined
  let savePromise: Promise<boolean> | undefined
  let actionController: AbortController | undefined
  let deletingProjectId: string | undefined
  const lifetime = new AbortController()

  function putProject(row: VideoProject) {
    const index = projects.value.findIndex((item) => item.id === row.id)
    if (index >= 0) projects.value.splice(index, 1, row)
    else projects.value.unshift(row)
  }
  function setDraft(value: VideoDraft | null) {
    suppressWatch = true
    draft.value = value
    suppressWatch = false
  }
  function reconcileSelection(shots: readonly VideoShotDraft[], selectPreview = false) {
    if (!shots.some((shot) => shot.id === selectedShotId.value)) selectedShotId.value = shots[0]?.id ?? ''
    selectedPreviewIds.value = selectedPreviewIds.value.filter((id) => shots.some((shot) => shot.id === id))
    if (selectPreview && !selectedPreviewIds.value.length && selectedShotId.value) selectedPreviewIds.value = [selectedShotId.value]
  }
  function activate(row: VideoProject, restore = true) {
    generation++
    acceptedVersion++
    actionController?.abort()
    acting.value = false
    if (saveTimer !== undefined) clearTimeout(saveTimer)
    project.value = row
    putProject(row)
    const restored = restore ? restoreDraft(row) : null
    setDraft(restored ?? fromProject(row))
    dirty.value = restored !== null
    saveError.value = ''
    error.value = ''
    selectedShotId.value = draft.value?.shots[0]?.id ?? ''
    selectedPreviewIds.value = selectedShotId.value ? [selectedShotId.value] : []
    trackId.value = row.track_id
    undoStack.value = []
  }
  function accept(row: VideoProject, version: number, preserveDraft = false) {
    acceptedVersion++
    project.value = row
    putProject(row)
    if (editVersion === version && !preserveDraft) {
      setDraft(fromProject(row))
      dirty.value = false
      try { sessionStorage.removeItem(draftKey(row.id)) } catch { /* Server draft is already durable. */ }
    } else if (draft.value) {
      try { sessionStorage.setItem(draftKey(row.id), JSON.stringify(snapshot(draft.value, row.revision))) } catch { /* Server save remains available. */ }
    }
    reconcileSelection(draft.value?.shots ?? [], true)
  }
  watch(draft, () => {
    if (suppressWatch || !project.value || !draft.value) return
    editVersion++
    dirty.value = true
    try { sessionStorage.setItem(draftKey(project.value.id), JSON.stringify(snapshot(draft.value, project.value.revision))) } catch { /* Saving on the server remains available. */ }
    if (saveTimer !== undefined) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => { void save() }, 500)
  }, { deep: true, flush: 'sync' })

  async function save(): Promise<boolean> {
    if (!alive || deletingProjectId !== undefined) return false
    if (savePromise) { const ok = await savePromise; if (!ok) return false; if (dirty.value && alive) return save(); return !dirty.value }
    if (!dirty.value) return true
    if (!project.value || !draft.value || active.value) return false
    const row = project.value
    const version = editVersion
    const token = generation
    let body: UpdateVideoProjectRequest
    try { body = parseUpdateVideoProjectRequest(snapshot(draft.value, row.revision)) }
    catch { saveError.value = 'invalid_draft'; return false }
    saving.value = true
    savePromise = (async () => {
      try {
        const result = await api.updateVideoProject(row.id, body, lifetime.signal)
        if (!alive || token !== generation) return false
        accept(result, version)
        saveError.value = ''
        return true
      } catch (cause) {
        if (alive && token === generation) saveError.value = api.videoRequestError(cause)
        return false
      } finally { saving.value = false; savePromise = undefined }
    })()
    return savePromise
  }
  async function selectProject(id: string) {
    if (!alive || acting.value || id === project.value?.id) return
    acting.value = true
    if (dirty.value && !await save()) { if (alive) acting.value = false; return }
    if (!alive) return
    const token = ++generation
    actionController?.abort()
    actionController = new AbortController()
    try {
      const row = await api.getVideoProject(id, actionController.signal)
      if (alive && token === generation) activate(row)
    } catch (cause) { if (alive && token === generation) error.value = api.videoRequestError(cause) }
    finally { if (alive && token === generation) acting.value = false }
  }
  async function reloadProject() {
    if (acting.value || !project.value) return
    const id = project.value.id
    const token = ++generation
    acting.value = true
    actionController?.abort(); actionController = new AbortController()
    try {
      const row = await api.getVideoProject(id, actionController.signal)
      if (!alive || token !== generation) return
      try { sessionStorage.removeItem(draftKey(id)) } catch { /* Reload is still explicit. */ }
      activate(row, false)
    } catch (cause) { if (alive && token === generation) error.value = api.videoRequestError(cause) }
    finally { if (alive) acting.value = false }
  }
  async function removeProject(id: string): Promise<boolean> {
    if (!alive || acting.value || loading.value) return false
    const row = project.value?.id === id ? project.value : projects.value.find((item) => item.id === id)
    if (!row || api.isVideoActive(row.job?.status)) return false
    const selected = project.value?.id === id
    acting.value = true
    deletingProjectId = id
    error.value = ''
    let token = generation
    acceptedVersion++
    if (saveTimer !== undefined) { clearTimeout(saveTimer); saveTimer = undefined }
    actionController?.abort()
    const controller = new AbortController()
    actionController = controller
    try {
      // An accepted save must settle before deletion can remove its destination.
      if (savePromise) await savePromise
      if (!alive || token !== generation) return false
      token = ++generation
      error.value = ''
      await api.deleteVideoProject(id, controller.signal)
      if (!alive || token !== generation) return false
      projects.value = projects.value.filter((item) => item.id !== id)
      try { sessionStorage.removeItem(draftKey(id)) } catch { /* Server removal remains authoritative. */ }
      if (selected) {
        project.value = null
        setDraft(null)
        dirty.value = false
        saveError.value = ''
        selectedShotId.value = ''
        selectedPreviewIds.value = []
        undoStack.value = []
        variantsPerShot.value = 1
        step.value = 'song'
      }
      return true
    } catch (cause) {
      if (alive && token === generation) {
        error.value = cause instanceof ApiError && i18n.global.te(`video.err.${cause.message}`)
          ? cause.message : 'project_delete_failed'
      }
      return false
    } finally {
      deletingProjectId = undefined
      if (alive) {
        generation++
        acceptedVersion++
        acting.value = false
        if (dirty.value && !active.value) {
          if (saveTimer !== undefined) clearTimeout(saveTimer)
          saveTimer = setTimeout(() => { void save() }, 500)
        }
      }
    }
  }
  async function createProject(request?: CreateVideoProjectRequest) {
    if (acting.value) return
    if (!request && !trackId.value) return
    acting.value = true
    if (dirty.value && !await save()) { acting.value = false; return }
    if (!alive) return
    const token = ++generation
    actionController?.abort()
    actionController = new AbortController()
    const seed = Math.floor(Math.random() * 2147483648)
    const body: CreateVideoProjectRequest = request ?? { track_id: trackId.value ?? undefined, name: tracks.value.find((item) => item.id === trackId.value)?.title || 'Video project', seed }
    try {
      const row = await api.createVideoProject(body.seed == null ? { ...body, seed } : body, actionController.signal)
      if (alive && token === generation) { activate(row, false); step.value = 'direction' }
    } catch (cause) { if (alive && token === generation) error.value = api.videoRequestError(cause) }
    finally { if (alive) acting.value = false }
  }
  type ProjectAction = (row: VideoProject, signal: AbortSignal) => Promise<VideoProject>
  async function action(run: ProjectAction, nextStep?: VideoWorkspaceStep, saveDraft = true) {
    if (!alive || acting.value || !project.value) return
    acting.value = true
    if (saveDraft && dirty.value && !await save()) { acting.value = false; return }
    if (!alive || !project.value) return
    const row = project.value
    const token = generation
    const version = editVersion
    error.value = ''
    actionController?.abort()
    actionController = new AbortController()
    try {
      const result = await run(row, actionController.signal)
      if (!alive || token !== generation) return
      if (result.id !== row.id) activate(result, false)
      else accept(result, version, !saveDraft && dirty.value)
      if (nextStep) step.value = nextStep
    } catch (cause) { if (alive && token === generation) error.value = api.videoRequestError(cause) }
    finally { if (alive && token === generation) acting.value = false }
  }
  function preview(ids = [selectedShotId.value]) {
    return action((row, signal) => api.previewVideoProject(row.id, { revision: row.revision, shot_ids: ids.filter(Boolean), variants_per_shot: variantsPerShot.value, reuse_completed: false }, signal), 'preview')
  }
  function render() { return action((row, signal) => api.renderVideoProject(row.id, { revision: row.revision, reuse_completed: true }, signal), 'export') }
  function analyze() { return action((row, signal) => api.analyzeVideoProject(row.id, { revision: row.revision }, signal), 'storyboard') }
  function approve(shotId: string, variantId: string) { return action((row, signal) => api.approveVideoVariant(row.id, shotId, { revision: row.revision, variant_id: variantId }, signal)) }
  function resume() { return action((row, signal) => api.resumeVideoProject(row.id, { revision: row.revision }, signal)) }
  function cancel() { return action((row, signal) => api.cancelVideoProject(row.id, signal), undefined, false) }
  function duplicate() { return action((row, signal) => api.duplicateVideoProject(row.id, { revision: row.revision }, signal), 'direction') }
  function exportVideo() { return action((row, signal) => api.exportVideoProject(row.id, { revision: row.revision, settings: draft.value?.export_settings }, signal)) }
  function upload(file: File) {
    if (file.size > 20 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { error.value = 'invalid_reference'; return }
    return action((row, signal) => api.uploadVideoReference(row.id, row.revision, file, signal))
  }
  function editShots(shots: VideoShotDraft[]) {
    if (!draft.value || readOnly.value) return
    undoStack.value.push(draft.value.shots.map((shot) => ({ ...shot })))
    if (undoStack.value.length > 50) undoStack.value.shift()
    draft.value.shots = shots
    reconcileSelection(shots)
  }
  function undo() {
    if (!draft.value || readOnly.value) return
    const previous = undoStack.value.pop()
    if (previous) { draft.value.shots = previous; reconcileSelection(previous) }
  }
  function addShot() {
    const cap = project.value?.preset === 'reel' ? 4 : 40
    if (!draft.value || readOnly.value || draft.value.shots.length >= cap) return
    const shot: VideoShotDraft = { id: newVideoId(), start_sec: frameTime(coverageEnd.value), seconds: 4, prompt: draft.value.direction || 'Describe this scene', seed: ((draft.value.seed ?? 42) + draft.value.shots.length) % 2147483648 }
    editShots([...draft.value.shots, shot]); selectedShotId.value = shot.id
  }
  const loop = createPollingLoop(async ({ signal, isCurrent }) => {
    const token = generation
    const accepted = acceptedVersion
    const id = project.value?.id
    try {
      const [old, busy, latest] = await Promise.all([api.listVideos(), api.otherWorkBusy(tracks.value.map((track) => track.id)), id ? api.getVideoProject(id, signal) : Promise.resolve(null)])
      if (!isCurrent() || !alive) return
      legacyVideos.value = old.videos
      serverBusy.value = busy
      if (latest && token === generation && accepted === acceptedVersion && !saving.value && !acting.value && latest.revision >= (project.value?.revision ?? 0)) {
        const revisionChanged = latest.revision !== project.value?.revision
        if (dirty.value && revisionChanged) { saveError.value = 'revision_conflict'; return }
        project.value = latest; putProject(latest)
        if (!dirty.value && !saving.value && !acting.value) { setDraft(fromProject(latest)); reconcileSelection(draft.value?.shots ?? []) }
        else if (revisionChanged && !saving.value && !acting.value) saveError.value = 'revision_conflict'
      }
      now.value = Date.now()
    } catch (cause) { if (isCurrent() && alive && token === generation) error.value = api.videoRequestError(cause) }
  }, 2000)
  onMounted(async () => {
    loading.value = true
    try {
      const [songs, saved, old, busy, setup] = await Promise.all([listTracks(), api.listVideoProjects(lifetime.signal), api.listVideos(), api.otherWorkBusy([]), api.videoReadiness(lifetime.signal).catch(() => null)])
      if (!alive) return
      tracks.value = songs; projects.value = saved.projects; legacyVideos.value = old.videos; serverBusy.value = busy
      readiness.value = setup
      trackId.value = songs[0]?.id ?? null
      if (saved.projects[0]) activate(saved.projects[0])
    } catch (cause) { if (alive) error.value = api.videoRequestError(cause) }
    finally {
      if (alive) { loading.value = false; loop.start(false); clock = setInterval(() => { now.value = Date.now() }, 1000) }
    }
  })
  onUnmounted(() => { alive = false; generation++; lifetime.abort(); actionController?.abort(); loop.stop(); if (clock !== undefined) clearInterval(clock); if (saveTimer !== undefined) clearTimeout(saveTimer) })
  return { tracks, projects, legacyVideos, project, draft, step, selectedShotId, selectedPreviewIds, variantsPerShot, trackId,
    selectedTrack, selectedShot, savedShot, loading, acting, saving, dirty, error, saveError, serverBusy, readiness, now, undoStack, active, readOnly, problem, coverageEnd, approvalCount,
    save, selectProject, reloadProject, removeProject, createProject, action, preview, render, analyze, approve, resume, cancel, duplicate, exportVideo, upload, editShots, undo, addShot }
}
