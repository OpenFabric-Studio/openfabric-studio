import { acceptHMRUpdate, defineStore } from 'pinia'
import { rememberGenerationJob, notifyGenerationComplete } from '../composables/completionNotifications'
import * as api from '../api/yue2'
import * as ownedApi from '../api/yueJobs'
import type { CotMode, GenerateOptions } from '../api/yue2'
import * as tracksApi from '../api/tracks'
import { applyStatus, getActiveVoiceId, VoiceApplyError, waitForVoiceApply } from '../api/voices'
import type { ApplyStatus } from '../api/voices'
import { forgetVoiceClock, rememberVoiceUsed, voiceClock, voiceUsed } from './voiceClock'
import { clearVoiceWatch, markVoiceWatch, noteVoice, voiceWatchActive } from './voiceWatch'
import type { JobStatus } from '../types'
import { i18n } from '../i18n'
import { createPollingLoop, type PollContext, type PollingLoop } from '../composables/polling'
import type { JsonObject, VoiceJobProgress, YueJobResponse, YueNativeProgress, YueGenerationSettings } from '../api/contracts'

const t = i18n.global.t

const HEALTH_MS = 5000
const jobLoops = new WeakMap<object, PollingLoop>()
const healthLoops = new WeakMap<object, PollingLoop>()
type HistoryEdit = { kind: 'renamed'; title: string } | { kind: 'deleted' }
// Only edits completed while this request is pending need reconciliation.
// Keep them outside persisted/reactive state and discard them with the load.
const pendingHistoryEdits = new WeakMap<object, Map<number | string, HistoryEdit>>()

export interface Yue2Job {
  id: string
  status: JobStatus | 'stopping'
  backendOwned?: boolean
  stage?: string
  queueReason?: string
  elapsedSeconds?: number
  phaseEtaSeconds?: number | null
  nativeProgress?: YueNativeProgress | null
  nativeProgressAvailable?: boolean
  createdAt: number
  title: string
  style: string
  lyrics: string
  cot: CotMode
  precision: 'q8_0' | 'q4_0'
  seed: number
  errorCode?: string
  error?: string
  audioUrl?: string
  abcPlan?: string | null
  durationSec?: number | null
  wallSec?: number | null
  savedFilename?: string | null
  saveError?: string | null
  dbId?: number | null
  shortId?: number | null
  finalized: boolean
  voiceApply?: 'running' | 'done' | 'failed'
  voicePhase?: string
  voiceId?: string
  voiceName?: string
  voiceStartedAt?: number
  voiceProgress?: VoiceJobProgress | null
  voiceError?: string
  voiceErrorCode?: string
  params?: JsonObject
}

export const useYue2Store = defineStore('yue2', {
  state: () => ({
    jobs: [] as Yue2Job[],
    health: null as api.HealthResponse | null,
    healthError: false,
    historyLoaded: false,
    historyError: '',
    _backgroundActive: false,
    _sessionGeneration: 0,
    _ownedGeneration: 0,
    // Set by a TrackCard's "insert into form" action; GenerateForm watches
    // this and copies it into its own local ABC textarea state.
    pendingAbcInsert: null as string | null,
    pendingParamsInsert: null as JsonObject | null,
    _voiceAborters: {} as Record<number, AbortController>,
    _historyGeneration: 0,
  }),
  actions: {
    requestInsertParams(params: JsonObject) {
      this.pendingParamsInsert = { ...params }
    },
    clearPendingParamsInsert() {
      this.pendingParamsInsert = null
    },
    async loadHistory() {
      const generation = ++this._historyGeneration
      const isCurrent = () => generation === this._historyGeneration
      const edits = new Map<number | string, HistoryEdit>()
      pendingHistoryEdits.set(this, edits)
      try {
        const [tracks, owned] = await Promise.all([
          tracksApi.listTracks('yue2'),
          ownedApi.list().catch(() => { if (isCurrent()) this.historyError = t('generationWorkspace.failed'); return null }),
        ])
        if (!isCurrent()) return
        const savedJobs: Yue2Job[] = await Promise.all(tracks.map(async (track) => {
          const voice = await this._savedVoice(track.id, track.audio_url, isCurrent)
          const job: Yue2Job = {
            id: `saved_${track.id}`,
            status: 'done',
            createdAt: new Date(track.created_at).getTime() || Date.now(),
            title: track.title,
            style: typeof track.params.style === 'string' && track.params.style.trim() ? track.params.style : track.title,
            lyrics: track.lyrics,
            cot: track.params.cot === 'melody' || track.params.cot === 'full' ? track.params.cot : 'off',
            precision: track.params.precision === 'q4_0' ? 'q4_0' : 'q8_0',
            seed: track.seed ?? 0,
            audioUrl: voice.audioUrl || track.audio_url,
            abcPlan: track.abc_url ? '' : null,
            durationSec: track.duration_ms ? track.duration_ms / 1000 : null,
            wallSec: track.wall_ms ? track.wall_ms / 1000 : null,
            savedFilename: track.filename,
            dbId: track.id,
            shortId: track.short_id ?? null,
            finalized: true,
            params: track.params,
            voiceApply: voice.voiceApply,
            voicePhase: voice.voicePhase,
            voiceId: voice.voiceId,
            voiceName: voice.voiceName,
            voiceStartedAt: voice.voiceStartedAt,
            voiceProgress: voice.voiceProgress,
            voiceError: voice.voiceError,
            voiceErrorCode: voice.voiceErrorCode,
          }
          return job
        }))
        if (!isCurrent()) return
        if (owned) {
          const reconciled = owned.flatMap(row => {
            if (edits.get(row.id)?.kind === 'deleted') return []
            const edit = row.track ? edits.get(row.track.id) : undefined
            if (edit?.kind === 'deleted') return []
            return [edit?.kind === 'renamed' && row.track ? { ...row, track: { ...row.track, title: edit.title } } : row]
          })
          this._mergeOwned(reconciled)
          for (const saved of savedJobs) {
            const live = this._trackJob(saved.dbId ?? -1)
            if (!live?.backendOwned || !saved.voiceApply) continue
            const { voiceApply, voicePhase, voiceId, voiceName, voiceStartedAt, voiceProgress, voiceError, voiceErrorCode } = saved
            Object.assign(live, { voiceApply, voicePhase, voiceId, voiceName, voiceStartedAt, voiceProgress, voiceError, voiceErrorCode })
          }
        }
        const ownedTrackIds = new Set(this.jobs.filter(job => job.backendOwned).flatMap(job => job.dbId == null ? [] : [job.dbId]))
        const currentSavedJobs = savedJobs.filter(job => !ownedTrackIds.has(job.dbId ?? -1)).flatMap(job => {
          const edit = job.dbId == null ? undefined : edits.get(job.dbId)
          if (edit?.kind === 'deleted') return []
          return [edit?.kind === 'renamed' ? { ...job, title: edit.title } : job]
        })
        // Keep any jobs still in-flight this session (not yet in the saved list).
        const inFlightIds = new Set(this.jobs.filter((j) => !j.finalized || j.backendOwned).map((j) => j.id))
        this.jobs = [...this.jobs.filter((j) => inFlightIds.has(j.id)), ...currentSavedJobs].sort((a, b) => b.createdAt - a.createdAt)
        for (const job of this.jobs) {
          if (job.voiceApply === 'running') this._followVoice(job)
          else if (job.voiceApply === 'done') void notifyGenerationComplete(`yue:${job.id}`, job.title)
        }
      } catch (cause) {
        if (isCurrent()) throw cause
      } finally {
        if (isCurrent()) {
          pendingHistoryEdits.delete(this)
          this.historyLoaded = true
        }
      }
    },
    _trackJob(trackId: number): Yue2Job | undefined {
      return this.jobs.find((job) => job.dbId === trackId)
    },
    _onVoice(trackId: number, row: ApplyStatus) {
      const live = this._trackJob(trackId)
      if (!live) return
      // The polling helper follows its terminal callback with a URL-only
      // result. Keep that measured receipt; a new active job must reset it.
      if (row.job_progress !== undefined || live.voiceProgress?.status !== row.status || row.status === 'queued' || row.status === 'running') live.voiceProgress = row.job_progress ?? null
      if (row.status === 'done') {
        if (row.audio_url) {
          if (live.audioUrl?.startsWith('blob:')) URL.revokeObjectURL(live.audioUrl)
          live.audioUrl = row.audio_url
        }
        live.voiceApply = 'done'
        if (live.backendOwned && live.status === 'done') void notifyGenerationComplete(`yue:${live.id}`, live.title)
        live.voicePhase = ''
        if (row.voice_id) live.voiceId = row.voice_id
        if (row.voice_name) live.voiceName = row.voice_name
        rememberVoiceUsed(trackId, live.voiceId || '', live.voiceName || '')
        forgetVoiceClock(trackId)
        return
      }
      if (row.status === 'failed' || row.status === 'cancelled' || row.status === 'idle') {
        live.voiceApply = 'failed'
        live.voicePhase = ''
        live.voiceErrorCode = row.error_code || 'interrupted'
        live.voiceError = row.error || ''
        forgetVoiceClock(trackId)
        return
      }
      const note = noteVoice(trackId, row)
      live.voiceApply = 'running'
      live.voicePhase = note.voicePhase
      live.voiceId = note.voiceId
      live.voiceName = note.voiceName
      live.voiceStartedAt = note.voiceStartedAt
    },
    async _savedVoice(trackId: number, audioUrl: string, isCurrent: () => boolean): Promise<{
      voiceApply?: Yue2Job['voiceApply']
      voicePhase?: string
      voiceId?: string
      voiceName?: string
      voiceStartedAt?: number
      voiceProgress?: VoiceJobProgress | null
      voiceError?: string
      voiceErrorCode?: string
      audioUrl?: string
    }> {
      try {
        const row = await applyStatus(trackId)
        if (!isCurrent()) return {}
        if (row.status === 'running' || row.status === 'queued') {
          const note = noteVoice(trackId, row)
          return { voiceApply: 'running', ...note }
        }
        if (row.status === 'done') {
          const used = voiceUsed(trackId)
          return {
            voiceApply: 'done',
            audioUrl: row.audio_url || audioUrl,
            voiceId: row.voice_id || used?.voiceId,
            voiceName: row.voice_name || used?.voiceName,
          }
        }
        if (row.status === 'failed' || row.status === 'cancelled') {
          return { voiceApply: 'failed', voiceError: row.error, voiceErrorCode: row.error_code }
        }
      } catch {
        // A track with no voice step still lists normally.
      }
      return {}
    },
    _followVoice(job: Yue2Job) {
      if (job.dbId == null || voiceWatchActive(job.dbId)) return
      const trackId = job.dbId
      markVoiceWatch(trackId)
      const controller = new AbortController()
      this._voiceAborters[trackId] = controller
      const clock = voiceClock(trackId)
      if (clock) {
        job.voiceId = job.voiceId || clock.voiceId
        job.voiceName = job.voiceName || clock.voiceName
        job.voiceStartedAt = job.voiceStartedAt || clock.startedAt
      }
      void waitForVoiceApply(trackId, (row) => {
        if (!controller.signal.aborted) this._onVoice(trackId, row)
      }, controller.signal).then((url) => {
        if (controller.signal.aborted) return
        this._onVoice(trackId, { status: 'done', audio_url: url, error: '', error_code: '', phase: '' })
      }).catch((err) => {
        if (controller.signal.aborted) return
        this._onVoice(trackId, {
          status: 'failed',
          audio_url: '',
          error: err instanceof VoiceApplyError ? err.detail : (err instanceof Error ? err.message : String(err)),
          error_code: err instanceof VoiceApplyError ? err.code : '',
          phase: '',
        })
      }).finally(() => {
        if (this._voiceAborters[trackId] !== controller) return
        delete this._voiceAborters[trackId]
        clearVoiceWatch(trackId)
      })
    },
    async refreshHealth(context?: PollContext) {
      try {
        const health = await api.health(context?.signal)
        if (context && !context.isCurrent()) return
        this.health = health
        this.healthError = false
      } catch {
        if (!context || context.isCurrent()) this.healthError = true
      }
    },
    _mergeOwned(rows: YueJobResponse[]) {
      for (const row of rows) {
        const track = row.track
        const existing = this.jobs.find(job => job.id === row.id || track != null && job.dbId === track.id)
        const mapped: Yue2Job = {
          id: row.id, backendOwned: true, status: row.status,
          createdAt: Date.parse(row.created_at), title: track?.title ?? row.title,
          style: row.options.style, lyrics: row.lyrics, cot: row.options.cot, precision: row.precision,
          seed: row.seed, stage: row.stage, queueReason: row.queue_reason, elapsedSeconds: row.elapsed_seconds,
          phaseEtaSeconds: row.phase_eta_seconds, nativeProgress: row.progress, nativeProgressAvailable: row.native_progress_available,
          finalized: row.status === 'done' || row.status === 'failed' || row.status === 'cancelled',
          errorCode: row.error_code,
          error: row.error_code ? t(`generationWorkspace.errors.${row.error_code}`) : undefined,
          audioUrl: existing?.audioUrl ?? track?.audio_url, savedFilename: track?.filename,
          dbId: track?.id, shortId: track?.short_id, durationSec: track?.duration_ms == null ? null : track.duration_ms / 1000,
          wallSec: track?.wall_ms == null ? null : track.wall_ms / 1000, abcPlan: track?.abc_url ? '' : null,
          params: track?.params ?? { ...row.options, lyrics: row.lyrics, precision: row.precision, seed: row.seed },
          voiceId: row.voice_id ?? existing?.voiceId,
        }
        if (!mapped.finalized) rememberGenerationJob(`yue:${row.id}`)
        if (row.status === 'done' && track && !row.voice_id) void notifyGenerationComplete(`yue:${row.id}`, mapped.title)
        if (existing) Object.assign(existing, mapped)
        else this.jobs.unshift(mapped)
      }
      this.jobs.sort((left, right) => right.createdAt - left.createdAt)
    },
    async pollOwnedJobs(context?: PollContext) {
      const token = this._sessionGeneration
      const ownership = this._ownedGeneration
      const isCurrent = () => token === this._sessionGeneration && ownership === this._ownedGeneration && (!context || context.isCurrent())
      try {
        const rows = await ownedApi.list(context?.signal)
        if (!isCurrent()) return
        this._mergeOwned(rows)
        this.historyError = ''
        for (const row of rows) {
          if (!isCurrent()) return
          if (row.status !== 'done' || !row.track || !row.voice_id) continue
          const voice = await this._savedVoice(row.track.id, row.track.audio_url, isCurrent)
          if (!isCurrent()) return
          const job = this._trackJob(row.track.id)
          if (!job) continue
          Object.assign(job, voice)
          if (job.voiceApply === 'running') this._followVoice(job)
          else if (job.voiceApply === 'done') void notifyGenerationComplete(`yue:${job.id}`, job.title)
        }
      } catch { if (isCurrent()) this.historyError = t('generationWorkspace.failed') }
    },
    startBackgroundTasks() {
      this._backgroundActive = true
      let jobs = jobLoops.get(this)
      if (!jobs) { jobs = createPollingLoop(context => this.pollOwnedJobs(context), 3000); jobLoops.set(this, jobs) }
      jobs.start()
      let loop = healthLoops.get(this)
      if (!loop) {
        loop = createPollingLoop((context) => this.refreshHealth(context), HEALTH_MS)
        healthLoops.set(this, loop)
      }
      loop.start()
    },
    stopBackgroundTasks() {
      this._backgroundActive = false
      this._sessionGeneration++
      jobLoops.get(this)?.stop()
      this._historyGeneration++
      pendingHistoryEdits.delete(this)
      healthLoops.get(this)?.stop()
      for (const [trackId, controller] of Object.entries(this._voiceAborters)) {
        controller.abort()
        delete this._voiceAborters[Number(trackId)]
        clearVoiceWatch(Number(trackId))
      }
    },
    async generateBatch(params: { lyrics: string; style: string; cot: CotMode; precision: 'q8_0' | 'q4_0'; baseSeed: number; randomSeed: boolean; batchSize: number; options: GenerateOptions; settings?: YueGenerationSettings; voiceId?: string | null }) {
      const token = this._sessionGeneration
      const voiceId = params.voiceId === undefined ? getActiveVoiceId() : params.voiceId
      const options = ownedApi.completeYueOptions(params.options)
      if (!Number.isInteger(params.batchSize) || params.batchSize < 1 || params.batchSize > 4) throw new TypeError(t('generationWorkspace.invalid'))
      for (let index = 0; index < params.batchSize; index++) {
        if (token !== this._sessionGeneration) return
        const seed = params.randomSeed ? Math.floor(Math.random() * 2147483647) : params.baseSeed + index
        const row = await ownedApi.submit({ title: params.style.slice(0, 500), lyrics: params.lyrics,
          seed, options, precision: params.precision, voice_id: voiceId, settings: params.settings ?? null })
        if (token !== this._sessionGeneration) return
        rememberGenerationJob(`yue:${row.id}`)
        this._mergeOwned([row])
      }
    },
    async cancel(jobId: string) {
      const job = this.jobs.find(row => row.id === jobId)
      if (!job || job.finalized && job.errorCode !== 'engine_recovery_required' || !job.backendOwned) return
      this._ownedGeneration++
      this._historyGeneration++
      job.status = 'stopping'
      job.finalized = false
      const token = this._sessionGeneration
      const row = await ownedApi.cancel(jobId)
      if (token !== this._sessionGeneration) return
      this._mergeOwned([row])
    },
    requestInsertAbc(abc: string) {
      this.pendingAbcInsert = abc
    },
    clearPendingAbcInsert() {
      this.pendingAbcInsert = null
    },
    async deleteJob(job: Yue2Job) {
      if (!job.finalized) throw new Error(t('generationWorkspace.errors.engine_recovery_required'))
      const trackId = job.dbId
      this._ownedGeneration++
      if (trackId != null) {
        await tracksApi.deleteTrack(trackId)
        this._ownedGeneration++
        pendingHistoryEdits.get(this)?.set(trackId, { kind: 'deleted' })
      }
      if (trackId == null && job.backendOwned) {
        await ownedApi.remove(job.id); this._ownedGeneration++
        pendingHistoryEdits.get(this)?.set(job.id, { kind: 'deleted' })
      }
      this.jobs = this.jobs.filter((j) => j.id !== job.id && (trackId == null || j.dbId !== trackId))
    },
    async renameJob(job: Yue2Job, title: string) {
      const trackId = job.dbId
      if (trackId == null) return
      this._ownedGeneration++
      const saved = await tracksApi.renameTrack(trackId, title)
      this._ownedGeneration++
      const edits = pendingHistoryEdits.get(this)
      if (edits?.get(trackId)?.kind !== 'deleted') edits?.set(trackId, { kind: 'renamed', title: saved.title })
      const target = this._trackJob(trackId)
      if (target) target.title = saved.title
    },
  },
})

// Without this, editing this file while the dev server is running leaves
// already-mounted components bound to a stale store instance (old action
// closures) instead of picking up the new code - looks like "it's fixed in
// a fresh tab but stuck in the one I already had open".
if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useYue2Store, import.meta.hot))
}
