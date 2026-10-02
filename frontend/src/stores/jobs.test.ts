// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useYue2Store } from './yue2'
import { useAceStepStore } from './aceStep'
import { useOrchestratorStore } from './orchestrator'
import * as yueOwned from '../api/yueJobs'
import { yueJobResponse } from './yueJobFixtures'
import * as yueApi from '../api/yue2'
import * as aceApi from '../api/aceStep'
import * as orchestratorApi from '../api/orchestrator'
import type { OrchestratorStatus } from '../types'
import * as tracksApi from '../api/tracks'
import type { AceJobResponse, SavedTrack, VoiceJobProgress } from '../api/contracts'
import { getActiveVoiceId, applyStatus, waitForVoiceApply } from '../api/voices'
import { useLoraTrainingStore } from './loraTraining'
import * as trainingApi from '../api/aceStepTraining'
import { clearVoiceWatch } from './voiceWatch'

vi.mock('../api/yueJobs', async original => ({ ...await original<typeof import('../api/yueJobs')>(), list: vi.fn().mockResolvedValue([]), submit: vi.fn(), cancel: vi.fn() }))
vi.mock('../api/yue2', () => ({ health: vi.fn(), generateTrack: vi.fn() }))
vi.mock('../api/aceStep', () => ({ health: vi.fn(), queryResult: vi.fn(), listJobs: vi.fn(), retrySave: vi.fn(), releaseTask: vi.fn(), adoptLegacyJob: vi.fn() }))
vi.mock('../api/orchestrator', () => ({ getStatus: vi.fn() }))
vi.mock('../api/tracks', () => ({ listTracks: vi.fn(), saveTrack: vi.fn() }))
vi.mock('../api/voices', () => ({ applyStatus: vi.fn().mockResolvedValue({ status: 'idle', error: '', error_code: '', audio_url: '' }), getActiveVoiceId: vi.fn().mockReturnValue('captured-voice'), waitForVoiceApply: vi.fn() }))
vi.mock('../api/aceStepTraining', () => ({ autoLabelStatus: vi.fn() }))

function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  vi.resetAllMocks()
  localStorage.clear()
  clearVoiceWatch(17)
  clearVoiceWatch(18)
  vi.mocked(yueOwned.list).mockResolvedValue([])
  vi.mocked(getActiveVoiceId).mockReturnValue('captured-voice')
  vi.mocked(applyStatus).mockResolvedValue({ status: 'idle', error: '', error_code: '', audio_url: '' })
})
afterEach(() => { vi.useRealTimers() })

describe('job cancellation', () => {
  it('cancels queued YuE2 jobs through the owned backend without starting browser inference', async () => {
    vi.mocked(yueOwned.submit).mockResolvedValue(yueJobResponse())
    vi.mocked(yueOwned.cancel).mockResolvedValue(yueJobResponse({ status: 'cancelled', stage: 'cancelled' }))
    const store = useYue2Store()
    await store.generateBatch({ lyrics: 'text', style: 'folk', cot: 'off', precision: 'q8_0', baseSeed: 1, randomSeed: false, batchSize: 1, options: { style: 'folk', cot: 'off' } })
    const queued = store.jobs.find(job => job.status === 'queued')
    if (!queued) throw new Error('Missing queued job')
    await store.cancel(queued.id)
    expect(queued.status).toBe('cancelled'); expect(queued.finalized).toBe(true)
    expect(yueOwned.cancel).toHaveBeenCalledWith(queued.id)
    expect(yueApi.generateTrack).not.toHaveBeenCalled()
  })
})

const saved: SavedTrack = { id: 17, short_id: 4, model: 'ace_step', created_at: '2026-10-01T10:00:00Z', title: 'Saved', lyrics: '', seed: null, duration_ms: 10_000, wall_ms: null, params: {}, filename: 'saved.wav', audio_url: '/api/tracks/17/audio', abc_url: null, stems: null, midi: null }
const completed: AceJobResponse = { task_id: 'task-1', status: 'done', created_at: saved.created_at, title: 'Batch', lyrics: '', audio_format: 'wav', batch_size: 2, params: {}, progress: 1, stage: 'saved', error: '', error_code: '', tracks: [saved], voice_id: null }
const voiceProgress: VoiceJobProgress = { job_id: 'apply-17', kind: 'apply', status: 'queued', queued_at: 100, observed_at: 200, phase: 'waiting_gpu', queue_reason: 'voice_training', queue_label: 'SackJo22' }

describe('backend ACE ownership', () => {
  it('adopts old browser-owned jobs once before discarding their local records', async () => {
    localStorage.setItem('openfabric_ace_inflight', JSON.stringify([{ id: 'task-1', title: 'Old task', lyrics: 'words', audioFormat: 'wav', batchSize: 2, createdAt: 100, params: { prompt: 'folk' } }]))
    vi.mocked(aceApi.adoptLegacyJob).mockResolvedValue(completed)
    vi.mocked(aceApi.listJobs).mockResolvedValue([completed])
    vi.mocked(tracksApi.listTracks).mockResolvedValue([saved])
    const store = useAceStepStore()
    await store.loadHistory()
    await store.loadHistory()
    expect(aceApi.adoptLegacyJob).toHaveBeenCalledTimes(1)
    expect(aceApi.adoptLegacyJob).toHaveBeenCalledWith({ task_id: 'task-1', title: 'Old task', voice_id: null, track_ids: [], params: { prompt: 'folk', audio_format: 'wav', batch_size: 2, lyrics: 'words' } })
    expect(localStorage.getItem('openfabric_ace_inflight')).toBeNull()
  })

  it('retains every original legacy record when any adoption fails', async () => {
    const original = JSON.stringify([{ id: 'task-1', params: {}, title: 'Old task' }, { id: 'task-2', params: {}, title: 'Other task' }])
    localStorage.setItem('openfabric_ace_inflight', original)
    vi.mocked(aceApi.adoptLegacyJob).mockResolvedValueOnce(completed).mockRejectedValueOnce(new Error('offline'))
    const store = useAceStepStore()
    await store.loadHistory()
    expect(localStorage.getItem('openfabric_ace_inflight')).toBe(original)
    expect(store.historyError).toBe('offline')
  })

  it('retains malformed legacy candidate IDs without submitting an adoption', async () => {
    const original = JSON.stringify([{ id: 'task-1', params: {}, title: 'Old task', dbIds: [-1] }])
    localStorage.setItem('openfabric_ace_inflight', original)
    vi.mocked(aceApi.adoptLegacyJob).mockResolvedValue(completed)
    vi.mocked(aceApi.listJobs).mockResolvedValue([completed])
    vi.mocked(tracksApi.listTracks).mockResolvedValue([saved])
    const store = useAceStepStore()
    await store.loadHistory()
    expect(aceApi.adoptLegacyJob).not.toHaveBeenCalled()
    expect(localStorage.getItem('openfabric_ace_inflight')).toBe(original)
    expect(store.historyError).toBeTruthy()
  })

  it('restores backend jobs and deduplicates their saved candidates on reload', async () => {
    vi.mocked(aceApi.listJobs).mockResolvedValue([completed])
    vi.mocked(tracksApi.listTracks).mockResolvedValue([saved])
    const store = useAceStepStore()
    await store.loadHistory()
    await store.loadHistory()
    expect(store.jobs).toHaveLength(1)
    expect(store.jobs[0]?.id).toBe('task-1')
    expect(store.jobs[0]?.dbIds).toEqual([17])
    expect(store.jobs[0]?.audioUrls).toEqual([saved.audio_url])
    expect(tracksApi.saveTrack).not.toHaveBeenCalled()
    store.stopBackgroundTasks()
  })

  it('keeps partially saved candidates visible and retries persistence through the backend', async () => {
    vi.mocked(aceApi.listJobs).mockResolvedValue([{ ...completed, status: 'failed', error_code: 'save_failed', error: 'Save interrupted' }])
    vi.mocked(tracksApi.listTracks).mockResolvedValue([saved])
    vi.mocked(aceApi.retrySave).mockResolvedValue(completed)
    const store = useAceStepStore()
    await store.loadHistory()
    expect(store.jobs[0]?.status).toBe('failed')
    expect(store.jobs[0]?.audioUrls).toEqual([saved.audio_url])
    await store.retrySave('task-1')
    expect(aceApi.retrySave).toHaveBeenCalledWith('task-1')
    expect(store.jobs[0]?.status).toBe('done')
    expect(tracksApi.saveTrack).not.toHaveBeenCalled()
  })

  it('does not claim voice conversion is running while a partial batch needs saving', async () => {
    vi.mocked(aceApi.listJobs).mockResolvedValue([{ ...completed, status: 'failed', error_code: 'save_failed', voice_id: 'captured-voice' }])
    vi.mocked(tracksApi.listTracks).mockResolvedValue([saved])
    const store = useAceStepStore()
    await store.loadHistory()
    expect(store.jobs[0]?.voiceApply).toBeUndefined()
    expect(store.jobs[0]?.status).toBe('failed')
  })

  it('captures the selected voice at submission', async () => {
    vi.mocked(aceApi.releaseTask).mockResolvedValue({ task_id: 'task-2', status: 'queued', queue_position: 1 })
    const store = useAceStepStore()
    await store.submit({ batch_size: 2 }, null, 'Title')
    expect(aceApi.releaseTask).toHaveBeenCalledWith({ batch_size: 2 }, null, 'Title', 'captured-voice', undefined, undefined)
    store.stopBackgroundTasks()
  })

  it('ignores generation results returned after background tasks stop', async () => {
    const response = deferred<AceJobResponse[]>()
    vi.mocked(aceApi.releaseTask).mockResolvedValue({ task_id: 'task-1', status: 'queued', queue_position: 1 })
    vi.mocked(aceApi.queryResult).mockReturnValue(response.promise)
    const store = useAceStepStore()
    await store.submit({}, null, 'Title')
    store.startBackgroundTasks()
    await vi.advanceTimersByTimeAsync(3000)
    expect(aceApi.queryResult).toHaveBeenCalledTimes(1)
    store.stopBackgroundTasks()
    response.resolve([completed])
    await vi.advanceTimersByTimeAsync(10_000)
    expect(store.jobs[0]?.status).toBe('queued')
    expect(aceApi.queryResult).toHaveBeenCalledTimes(1)
  })

  it('hydrates a job that finishes before the submission response reaches the browser', async () => {
    vi.mocked(aceApi.releaseTask).mockResolvedValue({ task_id: 'task-1', status: 'done', queue_position: 0 })
    vi.mocked(aceApi.queryResult).mockResolvedValue([completed])
    const store = useAceStepStore()
    await store.submit({}, null, 'Title')
    store.startBackgroundTasks()
    await vi.advanceTimersByTimeAsync(3000)
    expect(store.jobs[0]?.audioUrls).toEqual([saved.audio_url])
    expect(store.jobs[0]?.finalized).toBe(true)
    store.stopBackgroundTasks()
  })

  it('preserves every batch candidate while reconciling individual voice results', async () => {
    const other = { ...saved, id: 18, short_id: 5, audio_url: '/api/tracks/18/audio' }
    vi.mocked(aceApi.listJobs).mockResolvedValue([{ ...completed, tracks: [saved, other], voice_id: 'captured-voice' }])
    vi.mocked(tracksApi.listTracks).mockResolvedValue([saved, other])
    vi.mocked(applyStatus).mockImplementation(async (trackId) => ({ status: 'done', error: '', error_code: '', audio_url: `/voiced/${trackId}.wav`, voice_id: 'captured-voice', voice_name: 'Voice' }))
    const store = useAceStepStore()
    await store.loadHistory()
    expect(store.jobs).toHaveLength(1)
    expect(store.jobs[0]?.audioUrls).toEqual(['/voiced/17.wav', '/voiced/18.wav'])
    expect(store.jobs[0]?.voiceApply).toBe('done')
  })
})

describe('background poll lifecycle', () => {
  it('preserves measured queued progress through initial ACE and YuE2 history hydration', async () => {
    vi.mocked(aceApi.listJobs).mockResolvedValue([{ ...completed, voice_id: 'captured-voice' }])
    vi.mocked(tracksApi.listTracks).mockResolvedValue([saved])
    vi.mocked(applyStatus).mockResolvedValue({ status: 'queued', phase: 'waiting', error: '', error_code: '', audio_url: '', job_progress: voiceProgress })
    const ace = useAceStepStore(); await ace.loadHistory()
    expect(ace.jobs[0]?.voiceProgress).toEqual(voiceProgress)
    vi.mocked(tracksApi.listTracks).mockResolvedValue([{ ...saved, model: 'yue2' }])
    const observation = deferred<string>(); vi.mocked(waitForVoiceApply).mockReturnValue(observation.promise)
    const yue = useYue2Store(); await yue.loadHistory()
    expect(yue.jobs[0]?.voiceProgress).toEqual(voiceProgress)
    yue.stopBackgroundTasks(); ace.stopBackgroundTasks(); observation.resolve('/done.wav')
  })
  it('retains the measured terminal receipt after YuE2 voice polling resolves its audio URL', async () => {
    const store = useYue2Store()
    store.jobs = [{ id: 'saved_17', status: 'done', createdAt: 100, title: 'Title', style: 'Title', lyrics: '', cot: 'off', precision: 'q8_0', seed: 1, finalized: true, dbId: 17, voiceApply: 'running' }]
    const terminal = { ...voiceProgress, status: 'done', phase: 'complete', finished_at: 250 } satisfies VoiceJobProgress
    vi.mocked(waitForVoiceApply).mockImplementation(async (_trackId, onUpdate) => {
      onUpdate?.({ status: 'done', phase: 'mixing', error: '', error_code: '', audio_url: '/done.wav', job_progress: terminal })
      return '/done.wav'
    })
    store._followVoice(store.jobs[0]); await vi.advanceTimersByTimeAsync(0)
    expect(store.jobs[0]?.voiceProgress).toEqual(terminal)
    expect(store.jobs[0]?.voiceApply).toBe('done')
    store.stopBackgroundTasks()
  })
  it('does not replace YuE2 history or launch voice observers after teardown during a history request', async () => {
    const response = deferred<SavedTrack[]>()
    vi.mocked(tracksApi.listTracks).mockReturnValue(response.promise)
    vi.mocked(applyStatus).mockResolvedValue({ status: 'running', audio_url: '', error: '', error_code: '' })
    vi.mocked(waitForVoiceApply).mockResolvedValue('/voiced.wav')
    const store = useYue2Store()
    const load = store.loadHistory()
    store.stopBackgroundTasks()
    response.resolve([{ ...saved, model: 'yue2' }])
    await load
    expect(store.jobs).toEqual([])
    expect(waitForVoiceApply).not.toHaveBeenCalled()
  })

  it('cancels an owned YuE2 voice observer without marking backend conversion as failed', async () => {
    const response = deferred<string>()
    vi.mocked(waitForVoiceApply).mockReturnValue(response.promise)
    const store = useYue2Store()
    const job = { id: 'saved_17', status: 'done', createdAt: 100, title: 'Title', style: 'Title', lyrics: '', cot: 'off', precision: 'q8_0', seed: 1, finalized: true, dbId: 17, voiceApply: 'running' } satisfies import('./yue2').Yue2Job
    store.jobs = [job]
    store._followVoice(store.jobs[0])
    const signal = vi.mocked(waitForVoiceApply).mock.calls[0]?.[2]
    store.stopBackgroundTasks()
    expect(signal?.aborted).toBe(true)
    response.resolve('/late.wav')
    await vi.advanceTimersByTimeAsync(0)
    expect(store.jobs[0]?.voiceApply).toBe('running')
    expect(store.jobs[0]?.audioUrl).toBeUndefined()
  })

  it('does not restart LoRA auto-label polling after teardown during an awaited response', async () => {
    const response = deferred<trainingApi.AutoLabelStatus>()
    vi.mocked(trainingApi.autoLabelStatus).mockReturnValue(response.promise)
    const store = useLoraTrainingStore()
    store.autoLabelTaskId = 'old-task'
    store._pollAutoLabel()
    store.stopBackgroundTasks()
    response.resolve({ task_id: 'old-task', status: 'running', current: 2, total: 5, progress: 'still running' })
    await vi.advanceTimersByTimeAsync(10_000)
    expect(trainingApi.autoLabelStatus).toHaveBeenCalledTimes(1)
    expect(store.autoLabelCurrent).toBe(0)
  })
  it.each(['ace', 'yue2', 'orchestrator'] as const)('%s starts only one request and cannot restart after stop during await', async (name) => {
    const response = deferred<aceApi.HealthResponse & yueApi.HealthResponse & OrchestratorStatus>()
    const store = name === 'ace' ? useAceStepStore() : name === 'yue2' ? useYue2Store() : useOrchestratorStore()
    const request = name === 'ace' ? vi.mocked(aceApi.health) : name === 'yue2' ? vi.mocked(yueApi.health) : vi.mocked(orchestratorApi.getStatus)
    request.mockReturnValue(response.promise)
    const start = () => name === 'orchestrator' ? useOrchestratorStore().startPolling() : name === 'ace' ? useAceStepStore().startBackgroundTasks() : useYue2Store().startBackgroundTasks()
    const stop = () => name === 'orchestrator' ? useOrchestratorStore().stopPolling() : name === 'ace' ? useAceStepStore().stopBackgroundTasks() : useYue2Store().stopBackgroundTasks()
    start()
    start()
    expect(request).toHaveBeenCalledTimes(1)
    stop()
    request.mockRejectedValue(new Error('offline'))
    response.resolve({ status: 'ok', service: 'ace', version: '1', models_initialized: true, llm_initialized: true, loaded_model: 'test', loaded_lm_model: null, active_model: 'ace_step', models: { ace_step: { id: 'ace_step', label: 'ACE', status: 'running', error: null }, yue2: { id: 'yue2', label: 'YuE2', status: 'stopped', error: null } } })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(request).toHaveBeenCalledTimes(1)
    expect('health' in store ? store.health : store.activeModel).toBeNull()
  })
})
