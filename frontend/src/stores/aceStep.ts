import { acceptHMRUpdate, defineStore } from 'pinia'
import { rememberGenerationJob, notifyGenerationComplete } from '../composables/completionNotifications'
import * as api from '../api/aceStep'
import type { GenerateMusicRequest } from '../api/aceStep'
import * as tracksApi from '../api/tracks'
import type { SavedTrack } from '../api/tracks'
import { applyStatus, applyVoice, cancelVoiceApply, replaceVoice, getActiveVoiceId, VoiceApplyError } from '../api/voices'
import type { ApplyStatus } from '../api/voices'
import type { AceGenerationSettings, AceJobResponse, JsonObject, VoiceJobProgress } from '../api/contracts'
import { isJsonValue, parseAceAdoptRequest } from '../api/contracts'
import { isObject } from '../api/schemaValidation'
import { i18n } from '../i18n'
import type { JobStatus } from '../types'
import { createPollingLoop, type PollContext, type PollingLoop } from '../composables/polling'

const POLL_MS = 3000
const HEALTH_MS = 15000
const LEGACY_INFLIGHT_KEY = 'remiqora_ace_inflight'
const healthLoops = new WeakMap<object, PollingLoop>()
const jobLoops = new WeakMap<object, PollingLoop>()
const inventoryControllers = new WeakMap<object, AbortController>()
const voiceVersions = new WeakMap<object, Map<string, number>>()
const pendingVoiceActions = new WeakMap<object, Set<string>>()

function voiceVersion(store: object, id: string, advance = false): number {
  let versions = voiceVersions.get(store)
  if (!versions) { versions = new Map(); voiceVersions.set(store, versions) }
  const version = (versions.get(id) ?? 0) + (advance ? 1 : 0)
  versions.set(id, version)
  return version
}

function voiceActions(store: object): Set<string> {
  let actions = pendingVoiceActions.get(store)
  if (!actions) { actions = new Set(); pendingVoiceActions.set(store, actions) }
  return actions
}

export interface AceJob {
  id: string
  status: JobStatus
  createdAt: number
  title: string
  lyrics: string
  model?: string
  origin?: SavedTrack['model']
  audioFormat: 'mp3' | 'wav' | 'flac'
  batchSize: number
  error?: string
  errorCode?: string
  progress: number
  stage?: string
  audioUrls: string[]
  dbIds: number[]
  shortIds: number[]
  finalized: boolean
  backendOwned?: boolean
  voiceApply?: 'running' | 'done' | 'failed'
  voicePhase?: string
  voiceId?: string
  voiceName?: string
  voiceStartedAt?: number
  voiceProgress?: VoiceJobProgress | null
  voiceError?: string
  voiceErrorCode?: string
  voiceStates?: Record<number, ApplyStatus>
  voiceActionPending?: boolean
  durationSec?: number | null
  params?: JsonObject
}

function fromBackend(row: AceJobResponse): AceJob {
  return {
    id: row.task_id, status: row.status, createdAt: Date.parse(row.created_at),
    origin: 'ace_step',
    title: row.title, lyrics: row.lyrics,
    model: typeof row.params.model === 'string' ? row.params.model : undefined,
    audioFormat: row.audio_format, batchSize: row.batch_size,
    error: row.error, errorCode: row.error_code,
    progress: Math.round(row.progress * 100), stage: row.stage,
    audioUrls: row.tracks.map((track) => track.audio_url),
    dbIds: row.tracks.map((track) => track.id),
    shortIds: row.tracks.flatMap((track) => track.short_id == null ? [] : [track.short_id]),
    finalized: row.status !== 'queued' && row.status !== 'running',
    backendOwned: true, voiceId: row.voice_id || undefined,
    durationSec: row.tracks[0]?.duration_ms != null ? row.tracks[0].duration_ms / 1000 : null,
    params: row.params,
  }
}

function fromTrack(track: SavedTrack): AceJob {
  const format = track.params.audio_format
  const replacement = track.model === 'upload' && track.params.source === 'voice_replacement'
  return {
    id: `saved_${track.id}`, status: 'done', createdAt: Date.parse(track.created_at),
    origin: track.model,
    title: track.title, lyrics: track.lyrics,
    model: typeof track.params.model === 'string' ? track.params.model : undefined,
    audioFormat: format === 'wav' || format === 'flac' ? format : 'mp3',
    batchSize: 1, progress: 100, audioUrls: replacement ? [] : [track.audio_url], dbIds: [track.id],
    shortIds: track.short_id == null ? [] : [track.short_id], finalized: true,
    durationSec: track.duration_ms != null ? track.duration_ms / 1000 : null,
    params: track.params,
    voiceId: replacement && typeof track.params.voice_id === 'string' ? track.params.voice_id : undefined,
    voiceApply: replacement ? 'running' : undefined,
  }
}

export function isVoiceReplacement(job: AceJob): boolean {
  return job.origin === 'upload' && job.params?.source === 'voice_replacement'
}

function applyVoiceStates(job: AceJob, rows: Array<{ trackId: number; row: ApplyStatus }>): void {
  const replacement = isVoiceReplacement(job)
  const states: Record<number, ApplyStatus> = { ...job.voiceStates }
  for (const result of rows) {
    states[result.trackId] = result.row
    const index = job.dbIds.indexOf(result.trackId)
    if (index >= 0 && (!replacement || result.row.status === 'done')) {
      const url = result.row.audio_url || (replacement ? `/api/tracks/${result.trackId}/audio?v=${Date.now()}` : '')
      if (url) job.audioUrls[index] = url
    }
  }
  job.voiceStates = states
  const values = Object.values(states)
  const active = values.find((row) => row.status === 'queued' || row.status === 'running')
  const failed = values.find((row) => row.status === 'failed' || row.status === 'cancelled')
    ?? (replacement ? values.find((row) => row.status === 'idle') : undefined)
  const done = values.filter((row) => row.status === 'done')
  const voiceExpected = job.voiceId && (job.status === 'queued' || job.status === 'running' || job.status === 'done')
  if (active || (voiceExpected && (values.length < job.dbIds.length || (!replacement && values.some((row) => row.status === 'idle'))))) job.voiceApply = 'running'
  else if (failed) job.voiceApply = 'failed'
  else if (done.length && done.length === job.dbIds.length) job.voiceApply = 'done'
  else if (values.every((row) => row.status === 'idle')) job.voiceApply = undefined
  if (replacement && job.voiceApply !== 'done') job.audioUrls = []
  const selected = active || failed || done[0]
  if (selected) {
    job.voiceId = selected.voice_id || job.voiceId
    job.voiceName = selected.voice_name || job.voiceName
    job.voicePhase = selected.phase || ''
    job.voiceProgress = selected.job_progress ?? null
    job.voiceStartedAt = selected.started_at ? selected.started_at * 1000 : job.voiceStartedAt
    job.voiceError = selected.error
    job.voiceErrorCode = replacement && selected.status === 'idle' ? selected.error_code || 'interrupted' : selected.error_code
  }
}

export const useAceStepStore = defineStore('aceStep', {
  state: () => ({
    jobs: [] as AceJob[], historyLoaded: false,
    historyError: '',
    inventory: null as api.ModelInventory | null,
    inventoryLoading: false, inventoryError: '',
    health: null as api.HealthResponse | null, healthError: false,
    pendingParamsInsert: null as JsonObject | null,
    _historyGeneration: 0,
    _inventoryGeneration: 0,
    _backgroundActive: false,
    replacementSubmitting: false,
  }),
  getters: {
    activeJobs(state): AceJob[] {
      return state.jobs.filter((job) => job.status === 'queued' || job.status === 'running')
    },
  },
  actions: {
    requestInsertParams(params: JsonObject) { this.pendingParamsInsert = { ...params } },
    clearPendingParamsInsert() { this.pendingParamsInsert = null },
    async _adoptLegacyJobs(isCurrent: () => boolean) {
      let raw: string | null
      try { raw = localStorage.getItem(LEGACY_INFLIGHT_KEY) } catch { return }
      if (!raw) return
      const invalidHistory = () => new Error(i18n.global.t('storeErrors.invalidLegacyHistory'))
      let value: unknown
      try { value = JSON.parse(raw) } catch { throw invalidHistory() }
      if (!Array.isArray(value)) throw invalidHistory()
      const requests = value.map((row: unknown) => {
        if (!isObject(row)) throw invalidHistory()
        if (typeof row.id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(row.id)) throw invalidHistory()
        const trackIds = row.dbIds ?? []
        if (!Array.isArray(trackIds) || trackIds.length > 8 || trackIds.some((id: unknown) => typeof id !== 'number' || !Number.isInteger(id) || id <= 0)) throw invalidHistory()
        const oldParams = row.params ?? {}
        if (!isObject(oldParams) || !isJsonValue(oldParams)) throw invalidHistory()
        const params = { ...oldParams }
        if (row.audioFormat !== undefined && row.audioFormat !== 'mp3' && row.audioFormat !== 'wav' && row.audioFormat !== 'flac') throw invalidHistory()
        if (row.batchSize !== undefined && (typeof row.batchSize !== 'number' || !Number.isInteger(row.batchSize) || row.batchSize < 1 || row.batchSize > 8)) throw invalidHistory()
        if (row.lyrics !== undefined && typeof row.lyrics !== 'string') throw invalidHistory()
        params.audio_format ??= typeof row.audioFormat === 'string' ? row.audioFormat : 'mp3'
        params.batch_size ??= typeof row.batchSize === 'number' ? row.batchSize : 1
        params.lyrics ??= typeof row.lyrics === 'string' ? row.lyrics : ''
        if (params.model === undefined && typeof row.model === 'string') params.model = row.model
        try {
          return parseAceAdoptRequest({ task_id: row.id, title: row.title ?? '', params, voice_id: row.voiceId ?? null, track_ids: trackIds })
        } catch { throw invalidHistory() }
      })
      for (const request of requests) {
        if (!isCurrent()) return
        await api.adoptLegacyJob(request)
      }
      if (!isCurrent()) return
      // Another tab may still run the old client and append a task during migration.
      try { if (localStorage.getItem(LEGACY_INFLIGHT_KEY) === raw) localStorage.removeItem(LEGACY_INFLIGHT_KEY) } catch { /* Adoption remains idempotent if storage is unavailable. */ }
    },
    async loadHistory() {
      const generation = ++this._historyGeneration
      const existingIds = new Set(this.jobs.map((job) => job.id))
      this.historyError = ''
      try {
        await this._adoptLegacyJobs(() => generation === this._historyGeneration)
        if (generation !== this._historyGeneration) return
        const [rows, tracks, uploads] = await Promise.all([api.listJobs(), tracksApi.listTracks('ace_step'), tracksApi.listTracks('upload')])
        if (generation !== this._historyGeneration) return
        const candidateIds = new Set(rows.flatMap((row) => row.tracks.map((track) => track.id)))
        const restored = rows.map(fromBackend)
        const saved = new Map([...tracks, ...uploads.filter((track) => track.model === 'upload' && track.params.source === 'voice_replacement')]
          .filter((track) => !candidateIds.has(track.id)).map((track) => [track.id, track]))
        const history = [...restored, ...[...saved.values()].map(fromTrack)]
        const knownJobs = new Set(history.map((job) => job.id))
        const submittedDuringLoad = this.jobs.filter((job) => !existingIds.has(job.id) && !knownJobs.has(job.id))
        this.jobs = [...submittedDuringLoad, ...history]
          .sort((a, b) => b.createdAt - a.createdAt)
        const context: PollContext = { signal: new AbortController().signal, isCurrent: () => generation === this._historyGeneration }
        await Promise.all(this.jobs.map((job) => this._refreshVoices(job, context)))
        if (!context.isCurrent()) return
        for (const job of this.jobs) this._noteCompletion(job)
        this._ensurePolling()
      } catch (error) {
        if (generation === this._historyGeneration) this.historyError = error instanceof Error ? error.message : 'Unable to load generation history'
      } finally {
        if (generation === this._historyGeneration) this.historyLoaded = true
      }
    },
    async loadInventory() {
      const generation = ++this._inventoryGeneration
      inventoryControllers.get(this)?.abort()
      const controller = new AbortController()
      inventoryControllers.set(this, controller)
      this.inventoryLoading = true
      this.inventoryError = ''
      try {
        const inventory = await api.modelInventory(controller.signal)
        if (generation !== this._inventoryGeneration || controller.signal.aborted) return
        this.inventory = inventory
      } catch {
        if (generation === this._inventoryGeneration && !controller.signal.aborted) this.inventoryError = 'inventory_unavailable'
      } finally {
        if (generation === this._inventoryGeneration) {
          this.inventoryLoading = false
          inventoryControllers.delete(this)
        }
      }
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
    startBackgroundTasks() {
      this._backgroundActive = true
      let loop = healthLoops.get(this)
      if (!loop) {
        loop = createPollingLoop((context) => this.refreshHealth(context), HEALTH_MS)
        healthLoops.set(this, loop)
      }
      loop.start()
      this._ensurePolling()
    },
    stopBackgroundTasks() {
      this._backgroundActive = false
      healthLoops.get(this)?.stop()
      jobLoops.get(this)?.stop()
      ++this._historyGeneration
      ++this._inventoryGeneration
      inventoryControllers.get(this)?.abort()
      inventoryControllers.delete(this)
      this.inventoryLoading = false
    },
    _ensurePolling() {
      if (!this._backgroundActive) return
      if (!this.jobs.some((job) => !job.finalized || job.voiceApply === 'running')) return
      let loop = jobLoops.get(this)
      if (!loop) {
        loop = createPollingLoop(async (context) => {
          await this._pollActive(context)
          return this.jobs.some((job) => !job.finalized || job.voiceApply === 'running')
        }, POLL_MS)
        jobLoops.set(this, loop)
      }
      loop.start(false)
    },
    async _pollActive(context: PollContext) {
      const ids = this.jobs.filter((job) => job.backendOwned && !job.finalized).map((job) => job.id)
      if (ids.length) {
        try {
          const results = await api.queryResult(ids, context.signal)
          if (!context.isCurrent()) return
          for (const row of results) {
            const job = this.jobs.find((entry) => entry.id === row.task_id)
            if (job) this._applyResult(job, row)
          }
        } catch {
          // Generation and persistence continue on the backend; the next poll reconciles them.
        }
      }
      if (!context.isCurrent()) return
      await Promise.all(this.jobs.filter((job) => job.dbIds.length && (job.voiceApply === 'running' || (job.voiceId && !job.voiceApply))).map((job) => this._refreshVoices(job, context)))
    },
    _noteCompletion(job: AceJob) {
      if (!job.backendOwned) return
      if (!job.finalized) rememberGenerationJob(`ace:${job.id}`)
      else if (job.status === 'done' && job.dbIds.length && (!job.voiceId || job.voiceApply === 'done')) void notifyGenerationComplete(`ace:${job.id}`, job.title)
    },
    _applyResult(job: AceJob, row: AceJobResponse) {
      const next = fromBackend(row)
      Object.assign(job, next)
      this._noteCompletion(job)
    },
    async _refreshVoices(job: AceJob, context?: PollContext) {
      if (voiceActions(this).has(job.id)) { job.voiceActionPending = true; return }
      const version = voiceVersion(this, job.id)
      const rows = await Promise.all(job.dbIds.map(async (trackId) => {
        try { return { trackId, row: await applyStatus(trackId, context?.signal) } } catch { return null }
      }))
      if (context && !context.isCurrent() || version !== voiceVersion(this, job.id) || voiceActions(this).has(job.id)) return
      const live = this.jobs.find((entry) => entry.id === job.id)
      if (!live) return
      applyVoiceStates(live, rows.filter((row) => row !== null))
      this._noteCompletion(live)
    },
    async submit(req: GenerateMusicRequest, refAudioFile: File | null, title: string, styleAudioFile?: File | null, settings?: AceGenerationSettings): Promise<AceJob> {
      const voiceId = getActiveVoiceId()
      const result = await api.releaseTask(req, refAudioFile, title, voiceId, styleAudioFile, settings)
      const job: AceJob = {
        id: result.task_id, status: result.status, createdAt: Date.now(), title,
        origin: 'ace_step',
        lyrics: req.lyrics || '', model: req.model, audioFormat: req.audio_format || 'mp3',
        batchSize: req.batch_size || 1, progress: 0, audioUrls: [], dbIds: [], shortIds: [],
        finalized: false, backendOwned: true, voiceId: voiceId || undefined, params: { ...req },
      }
      this.jobs.unshift(job)
      rememberGenerationJob(`ace:${job.id}`)
      this._noteCompletion(job)
      this._ensurePolling()
      return job
    },
    async submitVoiceReplacement(file: File, voiceId: string): Promise<AceJob> {
      if (this.replacementSubmitting) throw new VoiceApplyError('replacement_active', '')
      this.replacementSubmitting = true
      try {
        const response = await replaceVoice(file, voiceId)
        const job = fromTrack(response.track)
        job.voiceId = response.application.voice_id || voiceId
        applyVoiceStates(job, [{ trackId: response.track.id, row: response.application }])
        const existing = this.jobs.findIndex((entry) => entry.id === job.id)
        if (existing >= 0) this.jobs.splice(existing, 1, job)
        else this.jobs.unshift(job)
        voiceVersion(this, job.id, true)
        this._ensurePolling()
        return job
      } finally { this.replacementSubmitting = false }
    },
    async _voiceReplacementAction(jobId: string, operation: 'cancel' | 'retry'): Promise<void> {
      const job = this.jobs.find((entry) => entry.id === jobId)
      const trackId = job?.dbIds[0]
      if (!job || !isVoiceReplacement(job) || !trackId) return
      if (voiceActions(this).has(jobId)) throw new VoiceApplyError('replacement_active', '')
      if (operation === 'retry' && !job.voiceId) throw new VoiceApplyError('not_ready', '')
      voiceActions(this).add(jobId)
      job.voiceActionPending = true
      voiceVersion(this, jobId, true)
      try {
        const row = operation === 'cancel' ? await cancelVoiceApply(trackId) : await applyVoice(job.voiceId || '', trackId)
        const live = this.jobs.find((entry) => entry.id === jobId)
        if (live) applyVoiceStates(live, [{ trackId, row }])
        this._ensurePolling()
      } finally {
        voiceVersion(this, jobId, true)
        voiceActions(this).delete(jobId)
        const live = this.jobs.find((entry) => entry.id === jobId)
        if (live) live.voiceActionPending = false
      }
    },
    cancelVoiceReplacement(jobId: string): Promise<void> { return this._voiceReplacementAction(jobId, 'cancel') },
    retryVoiceReplacement(jobId: string): Promise<void> { return this._voiceReplacementAction(jobId, 'retry') },
    async retrySave(jobId: string) {
      const row = await api.retrySave(jobId)
      const job = this.jobs.find((entry) => entry.id === jobId)
      if (job) this._applyResult(job, row)
      this._ensurePolling()
    },
    async cancel(jobId: string) {
      const current = this.jobs.find((entry) => entry.id === jobId)
      if (current && isVoiceReplacement(current)) { await this.cancelVoiceReplacement(jobId); return }
      const row = await api.cancelTask(jobId)
      const job = this.jobs.find((entry) => entry.id === jobId)
      if (job) this._applyResult(job, row)
    },
    async cancelAll() {
      await Promise.all(this.jobs.filter((job) => isVoiceReplacement(job) && job.voiceApply === 'running').map((job) => this.cancelVoiceReplacement(job.id)))
      await api.cancelAllTasks()
      await this.loadHistory()
    },
    async removeJob(jobId: string) {
      const job = this.jobs.find((entry) => entry.id === jobId)
      if (!job) return
      const observing = this._backgroundActive
      // A pre-deletion history snapshot may include candidates the browser has
      // not hydrated yet, under different saved-track IDs. Invalidate it before
      // awaiting removal and reconcile only after the backend finishes.
      ++this._historyGeneration
      try {
        if (isVoiceReplacement(job) && job.voiceApply === 'running') await this.cancelVoiceReplacement(jobId)
        if (job.backendOwned) await api.deleteJob(jobId)
        else await Promise.all(job.dbIds.map((trackId) => tracksApi.deleteTrack(trackId)))
        this.jobs = this.jobs.filter((entry) => entry.id !== jobId)
      } finally { if (!observing || this._backgroundActive) await this.loadHistory() }
    },
    async renameJob(jobId: string, title: string) {
      const job = this.jobs.find((entry) => entry.id === jobId)
      if (!job) return
      await Promise.all(job.dbIds.map((trackId) => tracksApi.renameTrack(trackId, title)))
      job.title = title
    },
  },
})

if (import.meta.hot) import.meta.hot.accept(acceptHMRUpdate(useAceStepStore, import.meta.hot))
