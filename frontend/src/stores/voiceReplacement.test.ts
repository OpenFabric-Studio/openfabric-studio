// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAceStepStore } from './aceStep'
import * as voices from '../api/voices'
import * as ace from '../api/aceStep'
import * as tracks from '../api/tracks'
import type { SavedTrack, ApplyStatusResponse, VoiceReplacementResponse, VoiceJobProgress } from '../api/contracts'

vi.mock('../api/voices', async (original) => ({ ...await original<typeof import('../api/voices')>(), replaceVoice: vi.fn(), applyVoice: vi.fn(), cancelVoiceApply: vi.fn(), applyStatus: vi.fn() }))
vi.mock('../api/aceStep', () => ({ listJobs: vi.fn(), releaseTask: vi.fn(), health: vi.fn(), queryResult: vi.fn(), cancelTask: vi.fn(), cancelAllTasks: vi.fn(), deleteJob: vi.fn() }))
vi.mock('../api/tracks', () => ({ listTracks: vi.fn(), deleteTrack: vi.fn(), saveTrack: vi.fn() }))

const voiceId = 'a'.repeat(32)
const source: SavedTrack = { id: 40, short_id: 10, model: 'upload', created_at: '2026-10-01T10:00:00Z', title: 'Original', lyrics: '', seed: null,
  duration_ms: 30_000, wall_ms: null, params: { source: 'voice_replacement_source' }, filename: 'original.wav', audio_url: '/api/tracks/40/audio', abc_url: null, stems: null, midi: null }
const result: SavedTrack = { ...source, id: 41, short_id: 11, title: 'Replacement', audio_url: '/api/tracks/41/audio',
  params: { source: 'voice_replacement', source_track_id: 40, voice_id: voiceId, audio_format: 'wav', task_type: 'voice_replacement' } }
const running: ApplyStatusResponse = { status: 'running', phase: 'separating', error: '', error_code: '', audio_url: '', voice_id: voiceId, voice_name: 'Singer', started_at: 1_000 }
const done: ApplyStatusResponse = { ...running, status: 'done', phase: 'mixing', audio_url: '/api/tracks/41/audio?v=converted' }
const cancelled: ApplyStatusResponse = { ...running, status: 'cancelled', error_code: 'cancelled' }
const file = () => new File(['original audio'], 'song.wav', { type: 'audio/wav' })

function deferred<T>() { let resolve: (value: T) => void = () => { throw new Error('Not initialized') }; const promise = new Promise<T>((finish) => { resolve = finish }); return { promise, resolve } }
beforeEach(() => {
  setActivePinia(createPinia()); vi.useFakeTimers(); vi.resetAllMocks(); localStorage.clear()
  vi.mocked(ace.listJobs).mockResolvedValue([])
  vi.mocked(tracks.listTracks).mockResolvedValue([])
  vi.mocked(voices.replaceVoice).mockResolvedValue({ track: result, source_track: source, application: running })
  vi.mocked(voices.applyStatus).mockResolvedValue(running)
  vi.mocked(voices.cancelVoiceApply).mockResolvedValue(cancelled)
  vi.mocked(voices.applyVoice).mockResolvedValue({ ...running, status: 'queued' })
})
afterEach(() => { useAceStepStore().stopBackgroundTasks(); vi.clearAllTimers(); vi.useRealTimers() })

it('submits replacement directly with immediate voice state and upload provenance, hiding dry audio', async () => {
  const store = useAceStepStore(); const audio = file()
  const job = await store.submitVoiceReplacement(audio, voiceId)
  expect(voices.replaceVoice).toHaveBeenCalledWith(audio, voiceId)
  expect(ace.releaseTask).not.toHaveBeenCalled(); expect(tracks.saveTrack).not.toHaveBeenCalled()
  expect(job).toMatchObject({ id: 'saved_41', origin: 'upload', status: 'done', voiceApply: 'running', voiceId, voiceName: 'Singer', voicePhase: 'separating', dbIds: [41], audioUrls: [], params: result.params })
  expect(job.backendOwned).not.toBe(true)
  expect(job.voiceStates?.[41]).toEqual(running)
})

it('exposes converted audio when the submission already completed', async () => {
  vi.mocked(voices.replaceVoice).mockResolvedValue({ track: result, source_track: source, application: done })
  const job = await useAceStepStore().submitVoiceReplacement(file(), voiceId)
  expect(job.voiceApply).toBe('done'); expect(job.audioUrls).toEqual([done.audio_url])
})
it('keeps measured replacement progress and replaces its timing on a retry', async () => {
  const progress: VoiceJobProgress = { job_id: 'apply-41', kind: 'apply', status: 'queued', queued_at: 100, observed_at: 200, phase: 'waiting_gpu', queue_reason: 'voice_training', queue_label: 'SackJo22' }
  vi.mocked(voices.replaceVoice).mockResolvedValue({ track: result, source_track: source, application: { ...running, status: 'queued', phase: 'waiting', job_progress: progress } })
  const store = useAceStepStore(); const job = await store.submitVoiceReplacement(file(), voiceId)
  expect(job.voiceProgress).toEqual(progress)
  await store.cancelVoiceReplacement(job.id)
  const retry = { ...progress, job_id: 'retry-41', queued_at: 300, observed_at: 300, queue_reason: '', queue_label: '' } satisfies VoiceJobProgress
  vi.mocked(voices.applyVoice).mockResolvedValue({ ...running, status: 'queued', phase: 'waiting', job_progress: retry })
  await store.retryVoiceReplacement(job.id)
  expect(store.jobs[0]?.voiceProgress).toEqual(retry)
})

it('loads only tagged result uploads, deduplicates them and restores conversion state', async () => {
  vi.mocked(tracks.listTracks).mockImplementation(async (origin) => origin === 'upload' ? [result, result, source, { ...source, id: 42, params: {} }] : [])
  const store = useAceStepStore(); await store.loadHistory()
  expect(tracks.listTracks).toHaveBeenCalledWith('upload')
  expect(store.jobs).toHaveLength(1)
  expect(store.jobs[0]).toMatchObject({ id: 'saved_41', origin: 'upload', voiceApply: 'running', voiceId, audioUrls: [], params: result.params })
  vi.mocked(voices.applyStatus).mockResolvedValue(done)
  await store.loadHistory()
  expect(store.jobs[0]?.voiceApply).toBe('done'); expect(store.jobs[0]?.audioUrls).toEqual([done.audio_url])
})

it('restores missing persisted conversion as interrupted instead of polling or exposing original bytes', async () => {
  vi.mocked(tracks.listTracks).mockImplementation(async (origin) => origin === 'upload' ? [result] : [])
  vi.mocked(voices.applyStatus).mockResolvedValue({ status: 'idle', error: '', error_code: '', audio_url: result.audio_url })
  const store = useAceStepStore(); await store.loadHistory()
  expect(store.jobs[0]).toMatchObject({ voiceApply: 'failed', voiceErrorCode: 'interrupted', audioUrls: [] })
})

it('preserves and deduplicates replacement submissions while an older history request resolves', async () => {
  const history = deferred<SavedTrack[]>()
  vi.mocked(tracks.listTracks).mockImplementation((origin) => origin === 'upload' ? history.promise : Promise.resolve([]))
  const store = useAceStepStore(); const loading = store.loadHistory()
  await vi.advanceTimersByTimeAsync(0)
  await store.submitVoiceReplacement(file(), voiceId)
  history.resolve([]); await loading
  expect(store.jobs.map((job) => job.id)).toEqual(['saved_41'])
  vi.mocked(tracks.listTracks).mockImplementation(async (origin) => origin === 'upload' ? [result] : [])
  await store.loadHistory()
  expect(store.jobs.map((job) => job.id)).toEqual(['saved_41'])
})

it('blocks duplicate uploads while a submission is pending and permits the next submission after failure', async () => {
  const pending = deferred<VoiceReplacementResponse>()
  vi.mocked(voices.replaceVoice).mockReturnValue(pending.promise)
  const store = useAceStepStore(); const first = store.submitVoiceReplacement(file(), voiceId)
  await expect(store.submitVoiceReplacement(file(), voiceId)).rejects.toThrow()
  expect(voices.replaceVoice).toHaveBeenCalledTimes(1)
  pending.resolve({ track: result, source_track: source, application: running }); await first
  vi.mocked(voices.replaceVoice).mockRejectedValueOnce(new Error('upload failed'))
  await expect(store.submitVoiceReplacement(file(), voiceId)).rejects.toThrow('upload failed')
  vi.mocked(voices.replaceVoice).mockResolvedValue({ track: result, source_track: source, application: running })
  await store.submitVoiceReplacement(file(), voiceId)
  expect(store.jobs).toHaveLength(1)
})

it('cancels replacement conversion without an ACE cancellation and ignores an older poll result', async () => {
  const store = useAceStepStore(); const job = await store.submitVoiceReplacement(file(), voiceId)
  const response = deferred<ApplyStatusResponse>(); vi.mocked(voices.applyStatus).mockReturnValue(response.promise)
  const polling = store._refreshVoices(store.jobs[0])
  await store.cancel(job.id)
  response.resolve(running); await polling
  expect(voices.cancelVoiceApply).toHaveBeenCalledWith(41)
  expect(ace.cancelTask).not.toHaveBeenCalled()
  expect(store.jobs[0]).toMatchObject({ voiceApply: 'failed', voiceErrorCode: 'cancelled', audioUrls: [] })
})

it('cancel-all drains replacement conversion as well as backend generation', async () => {
  const store = useAceStepStore(); await store.submitVoiceReplacement(file(), voiceId)
  vi.mocked(tracks.listTracks).mockImplementation(async (origin) => origin === 'upload' ? [result] : [])
  vi.mocked(voices.applyStatus).mockResolvedValue(cancelled)
  await store.cancelAll()
  expect(voices.cancelVoiceApply).toHaveBeenCalledWith(41)
  expect(ace.cancelAllTasks).toHaveBeenCalledTimes(1)
  expect(store.jobs[0]?.voiceErrorCode).toBe('cancelled')
})

it('retries with the captured voice and keeps dry audio hidden until conversion completes', async () => {
  const store = useAceStepStore(); const job = await store.submitVoiceReplacement(file(), voiceId)
  await store.cancelVoiceReplacement(job.id)
  await store.retryVoiceReplacement(job.id)
  expect(voices.applyVoice).toHaveBeenCalledWith(voiceId, 41)
  expect(store.jobs[0]?.voiceApply).toBe('running'); expect(store.jobs[0]?.audioUrls).toEqual([])
})

it('keeps a pending cancellation owned through history reload and blocks a competing retry', async () => {
  const store = useAceStepStore(); const job = await store.submitVoiceReplacement(file(), voiceId)
  const response = deferred<ApplyStatusResponse>(); vi.mocked(voices.cancelVoiceApply).mockReturnValue(response.promise)
  vi.mocked(tracks.listTracks).mockImplementation(async (origin) => origin === 'upload' ? [result] : [])
  const cancellation = store.cancelVoiceReplacement(job.id)
  await store.loadHistory()
  expect(store.jobs[0]?.voiceActionPending).toBe(true)
  await expect(store.retryVoiceReplacement(job.id)).rejects.toThrow()
  expect(voices.applyVoice).not.toHaveBeenCalled()
  expect(voices.applyStatus).not.toHaveBeenCalled()
  response.resolve(cancelled); await cancellation
  expect(store.jobs[0]).toMatchObject({ voiceApply: 'failed', voiceErrorCode: 'cancelled', voiceActionPending: false })
})

it('drains running conversion before deletion and keeps the independent original track', async () => {
  const store = useAceStepStore(); const job = await store.submitVoiceReplacement(file(), voiceId)
  const response = deferred<ApplyStatusResponse>(); vi.mocked(voices.cancelVoiceApply).mockReturnValue(response.promise)
  const removal = store.removeJob(job.id)
  expect(tracks.deleteTrack).not.toHaveBeenCalled()
  response.resolve(cancelled); await removal
  expect(tracks.deleteTrack).toHaveBeenCalledWith(41)
  expect(tracks.deleteTrack).not.toHaveBeenCalledWith(40)
  expect(store.jobs).toEqual([])
})

it('keeps the result and original when conversion cannot be safely cancelled before deletion', async () => {
  const store = useAceStepStore(); const job = await store.submitVoiceReplacement(file(), voiceId)
  vi.mocked(voices.cancelVoiceApply).mockRejectedValue(new Error('cleanup failed'))
  vi.mocked(tracks.listTracks).mockImplementation(async (origin) => origin === 'upload' ? [result] : [])
  await expect(store.removeJob(job.id)).rejects.toThrow('cleanup failed')
  expect(tracks.deleteTrack).not.toHaveBeenCalled()
  expect(store.jobs).toHaveLength(1)
})

it('does not restore a deleted replacement from an older history snapshot and preserves a concurrent submission', async () => {
  const store = useAceStepStore(); const removed = await store.submitVoiceReplacement(file(), voiceId)
  const history = deferred<SavedTrack[]>()
  const newer = { ...result, id: 43, short_id: 12, audio_url: '/api/tracks/43/audio' }
  let uploadReads = 0
  vi.mocked(tracks.listTracks).mockImplementation((origin) => {
    if (origin !== 'upload') return Promise.resolve([])
    uploadReads++
    return uploadReads === 1 ? history.promise : Promise.resolve([newer])
  })
  const loading = store.loadHistory(); await vi.advanceTimersByTimeAsync(0)
  await store.removeJob(removed.id)
  vi.mocked(voices.replaceVoice).mockResolvedValue({ track: newer, source_track: source, application: { ...running } })
  await store.submitVoiceReplacement(file(), voiceId)
  history.resolve([result]); await loading
  expect(store.jobs.map((job) => job.id)).toEqual(['saved_43'])
  expect(store.jobs[0]?.voiceApply).toBe('running')
  expect(store.historyLoaded).toBe(true)
})

it('does not resurrect unhydrated deleted ACE candidates under saved-track aliases', async () => {
  const store = useAceStepStore()
  vi.mocked(ace.releaseTask).mockResolvedValue({ task_id: 'finished-ace', status: 'done', queue_position: 0 })
  const removed = await store.submit({}, null, 'Finished before hydration')
  expect(removed.dbIds).toEqual([])
  const history = deferred<SavedTrack[]>()
  let aceReads = 0
  vi.mocked(tracks.listTracks).mockImplementation((origin) => {
    if (origin !== 'ace_step') return Promise.resolve([])
    aceReads++
    return aceReads === 1 ? history.promise : Promise.resolve([])
  })
  const loading = store.loadHistory(); await vi.advanceTimersByTimeAsync(0)
  await store.removeJob(removed.id)
  history.resolve([{ ...result, id: 47, model: 'ace_step', params: {} }]); await loading
  expect(ace.deleteJob).toHaveBeenCalledWith('finished-ace')
  expect(store.jobs).toEqual([])
})

it('does not restart history observation after teardown while deletion is awaited', async () => {
  const store = useAceStepStore(); const job = await store.submitVoiceReplacement(file(), voiceId)
  const deleted = deferred<void>(); vi.mocked(tracks.deleteTrack).mockReturnValue(deleted.promise)
  store.startBackgroundTasks()
  const removal = store.removeJob(job.id); await vi.advanceTimersByTimeAsync(0)
  expect(tracks.deleteTrack).toHaveBeenCalledWith(41)
  store.stopBackgroundTasks(); deleted.resolve(undefined); await removal
  expect(tracks.listTracks).not.toHaveBeenCalled()
  expect(ace.listJobs).not.toHaveBeenCalled()
  expect(store.jobs).toEqual([])
})

it('ignores conversion polls returned after stopping background tasks', async () => {
  const store = useAceStepStore(); await store.submitVoiceReplacement(file(), voiceId)
  const response = deferred<ApplyStatusResponse>(); vi.mocked(voices.applyStatus).mockReturnValue(response.promise)
  store.startBackgroundTasks(); await vi.advanceTimersByTimeAsync(3000)
  store.stopBackgroundTasks(); response.resolve(done); await vi.advanceTimersByTimeAsync(10_000)
  expect(store.jobs[0]?.voiceApply).toBe('running'); expect(store.jobs[0]?.audioUrls).toEqual([])
  expect(voices.applyStatus).toHaveBeenCalledTimes(1)
})
