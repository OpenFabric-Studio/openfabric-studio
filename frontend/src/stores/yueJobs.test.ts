// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useYue2Store } from './yue2'
import * as api from '../api/yueJobs'
import * as tracks from '../api/tracks'
import { yueJobResponse } from './yueJobFixtures'
import type { YueJobResponse, SavedTrack } from '../api/contracts'
vi.mock('../api/yueJobs', async original => ({ ...await original<typeof import('../api/yueJobs')>(), list: vi.fn(), submit: vi.fn(), cancel: vi.fn(), remove: vi.fn() }))
vi.mock('../api/yue2', async original => ({ ...await original<typeof import('../api/yue2')>(), health: vi.fn().mockResolvedValue({ status: 'ok' }) }))
vi.mock('../api/tracks', async original => ({ ...await original<typeof import('../api/tracks')>(), listTracks: vi.fn(), saveTrack: vi.fn() }))
vi.mock('../api/voices', async original => ({ ...await original<typeof import('../api/voices')>(), applyStatus: vi.fn().mockResolvedValue({ status: 'idle', error: '', error_code: '', audio_url: '' }) }))
const track: SavedTrack = { id: 7, short_id: 7, model: 'yue2', title: 'Jazz', filename: 'jazz.wav', created_at: '2026-10-01T10:00:00Z', lyrics: 'lyrics', seed: 1, duration_ms: 4000, wall_ms: 3000, params: { style: 'jazz' }, audio_url: '/api/tracks/7/audio', abc_url: null, stems: null, midi: null }
beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks(); localStorage.clear(); vi.mocked(api.list).mockResolvedValue([]); vi.mocked(tracks.listTracks).mockResolvedValue([]) })
afterEach(() => vi.useRealTimers())
function deferred<T>() { let resolve = (_value: T): void => { throw new Error('Uninitialized') }; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
it('restores active server jobs on reload and does not duplicate saved audio', async () => {
  vi.mocked(api.list).mockResolvedValue([yueJobResponse({ status: 'running', stage: 'generating' }), yueJobResponse({ id: 'b'.repeat(32), status: 'done', stage: 'done', track })]); vi.mocked(tracks.listTracks).mockResolvedValue([track])
  const store = useYue2Store(); await store.loadHistory()
  expect(store.jobs).toHaveLength(2); expect(store.jobs.find(job => job.dbId === 7)?.id).toBe('b'.repeat(32)); expect(tracks.saveTrack).not.toHaveBeenCalled()
})
it('keeps stopping jobs active until the backend confirms cancellation', async () => {
  vi.mocked(api.list).mockResolvedValue([yueJobResponse({ status: 'running' })]); const store = useYue2Store(); await store.loadHistory()
  const request = deferred<YueJobResponse>(); vi.mocked(api.cancel).mockReturnValue(request.promise)
  const cancellation = store.cancel('a'.repeat(32)); expect(store.jobs[0]?.status).toBe('stopping'); expect(store.jobs[0]?.finalized).toBe(false)
  request.resolve(yueJobResponse({ status: 'cancelled', stage: 'cancelled' })); await cancellation
  expect(store.jobs[0]?.status).toBe('cancelled'); expect(store.jobs[0]?.finalized).toBe(true)
})
it('ignores delayed polling after teardown and never restarts it', async () => {
  const pending = deferred<YueJobResponse[]>(); vi.mocked(api.list).mockReturnValue(pending.promise)
  const store = useYue2Store(); store.startBackgroundTasks(); store.stopBackgroundTasks()
  pending.resolve([yueJobResponse()]); await Promise.resolve(); await Promise.resolve(); await vi.advanceTimersByTimeAsync(10000)
  expect(store.jobs).toEqual([]); expect(api.list).toHaveBeenCalledTimes(1)
})
it('preserves a selected playback URL when an unchanged completed job is polled again', async () => {
  vi.mocked(api.list).mockResolvedValue([yueJobResponse({ status: 'done', track })]); const store = useYue2Store(); await store.loadHistory()
  const job = store.jobs[0]; if (!job) throw new Error('Job missing'); job.audioUrl = '/selected-voice.wav'
  await store.pollOwnedJobs(); expect(store.jobs[0]).toBe(job); expect(job.audioUrl).toBe('/selected-voice.wav')
})
it('reconciles renamed owned tracks against a pending history snapshot', async () => {
  const completed = yueJobResponse({ status: 'done', track })
  vi.mocked(api.list).mockResolvedValue([completed]); vi.mocked(tracks.listTracks).mockResolvedValue([track])
  const store = useYue2Store(); await store.loadHistory(); const job = store.jobs[0]; if (!job) throw new Error('Missing job')
  const snapshot = deferred<YueJobResponse[]>(); vi.mocked(api.list).mockReturnValueOnce(snapshot.promise)
  vi.spyOn(tracks, 'renameTrack').mockResolvedValue({ ...track, title: 'Newest title' })
  const history = store.loadHistory(); await store.renameJob(job, 'Newest title')
  snapshot.resolve([completed]); await history
  expect(store.jobs[0]?.title).toBe('Newest title')
})
it('does not let an old poll reverse a successful rename', async () => {
  vi.mocked(api.list).mockResolvedValue([yueJobResponse({ status: 'done', track })]); const store = useYue2Store(); await store.loadHistory(); const job = store.jobs[0]; if (!job) throw new Error('Missing job')
  const snapshot = deferred<YueJobResponse[]>(); vi.mocked(api.list).mockReturnValueOnce(snapshot.promise)
  vi.spyOn(tracks, 'renameTrack').mockResolvedValue({ ...track, title: 'Latest title' })
  const poll = store.pollOwnedJobs(); await store.renameJob(job, 'Latest title'); snapshot.resolve([yueJobResponse({ status: 'done', track })]); await poll
  expect(job.title).toBe('Latest title')
})

it('discards a terminal no-track job on the backend and preserves it when discard fails', async () => {
  vi.mocked(api.list).mockResolvedValue([yueJobResponse({ status: 'failed' })]); const store = useYue2Store(); await store.loadHistory(); const job = store.jobs[0]; if (!job) throw new Error('Missing job')
  vi.mocked(api.remove).mockRejectedValueOnce(new Error('unavailable')); await expect(store.deleteJob(job)).rejects.toThrow('unavailable'); expect(store.jobs).toHaveLength(1)
  vi.mocked(api.remove).mockResolvedValue({ deleted: true }); await store.deleteJob(job); expect(api.remove).toHaveBeenCalledWith(job.id); expect(store.jobs).toEqual([])
})
it('does not resurrect a discarded no-track job from an in-flight history response', async () => {
  const failed = yueJobResponse({ status: 'failed' }); vi.mocked(api.list).mockResolvedValue([failed]); const store = useYue2Store(); await store.loadHistory(); const job = store.jobs[0]; if (!job) throw new Error('Missing job')
  const snapshot = deferred<YueJobResponse[]>(); vi.mocked(api.list).mockReturnValueOnce(snapshot.promise); vi.mocked(api.remove).mockResolvedValue({ deleted: true })
  const history = store.loadHistory(); await store.deleteJob(job); snapshot.resolve([failed]); await history
  expect(store.jobs).toEqual([])
})
