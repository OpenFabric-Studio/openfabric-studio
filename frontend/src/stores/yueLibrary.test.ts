// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useYue2Store } from './yue2'
import * as tracks from '../api/tracks'
import { parseSavedTrack } from '../api/contracts'
import * as voices from '../api/voices'
import type { ApplyStatus } from '../api/voices'
vi.mock('../api/yueJobs', async original => ({ ...await original<typeof import('../api/yueJobs')>(), list: vi.fn().mockResolvedValue([]) }))
vi.mock('../api/tracks', async original => ({ ...await original<typeof import('../api/tracks')>(), listTracks: vi.fn(), renameTrack: vi.fn(), deleteTrack: vi.fn() }))
vi.mock('../api/voices', async original => ({ ...await original<typeof import('../api/voices')>(), applyStatus: vi.fn().mockResolvedValue({ status: 'idle', error: '', error_code: '', audio_url: '' }) }))
const idle: ApplyStatus = { status: 'idle', error: '', error_code: '', audio_url: '' }
function saved(style: unknown = 'folk, acoustic guitar') { return parseSavedTrack({ id: 7, short_id: 7, model: 'yue2', title: 'Renamed song', filename: 'song.wav', created_at: '2026-10-01T10:00:00Z', lyrics: '[Verse]\nHello', seed: 4, duration_ms: 4000, wall_ms: null, params: { style }, audio_url: '/song.wav', abc_url: null, stems: null, midi: null }) }
beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks(); vi.mocked(tracks.listTracks).mockResolvedValue([saved()]) })
it('restores a saved title separately from the original generation style', async () => {
  const store = useYue2Store(); await store.loadHistory()
  expect(store.jobs[0]).toMatchObject({ title: 'Renamed song', style: 'folk, acoustic guitar' })
})
it('renames only the title and preserves style across another history load', async () => {
  const store = useYue2Store(); await store.loadHistory(); const job = store.jobs[0]
  if (!job) throw new Error('Missing saved job')
  vi.mocked(tracks.renameTrack).mockResolvedValue({ ...saved(), title: 'Another title' })
  await store.renameJob(job, 'Another title')
  expect(store.jobs[0]).toMatchObject({ title: 'Another title', style: 'folk, acoustic guitar' })
  vi.mocked(tracks.listTracks).mockResolvedValue([{ ...saved(), title: 'Another title' }]); await store.loadHistory()
  expect(store.jobs[0]).toMatchObject({ title: 'Another title', style: 'folk, acoustic guitar' })
})
it.each([null, 4, [1], {}, '', '  '].map(style => [style]))('falls back to the saved title for malformed or absent style %j', async style => {
  vi.mocked(tracks.listTracks).mockResolvedValue([saved(style)]); const store = useYue2Store(); await store.loadHistory()
  expect(store.jobs[0]).toMatchObject({ title: 'Renamed song', style: 'Renamed song' })
})
it('keeps a saved card when backend deletion fails', async () => {
  const store = useYue2Store(); await store.loadHistory(); const job = store.jobs[0]
  if (!job) throw new Error('Missing saved job')
  vi.mocked(tracks.deleteTrack).mockRejectedValue(new Error('offline'))
  await expect(store.deleteJob(job)).rejects.toThrow('offline')
  expect(store.jobs).toHaveLength(1)
})

function deferred<T>() {
  let resolve = (_value: T): void => { throw new Error('Not initialized') }
  let reject = (_cause: unknown): void => { throw new Error('Not initialized') }
  const promise = new Promise<T>((finish, fail) => { resolve = finish; reject = fail })
  return { promise, resolve, reject }
}
async function settle() { for (let i = 0; i < 4; i++) await Promise.resolve() }

it.each(['rename', 'delete'] as const)('keeps successful %s edits when pending voice history finishes and retains other valid songs', async action => {
  const store = useYue2Store(); await store.loadHistory()
  const job = store.jobs[0]; if (!job) throw new Error('Missing saved job')
  const voice = deferred<ApplyStatus>()
  vi.mocked(voices.applyStatus).mockReturnValueOnce(voice.promise)
  vi.mocked(tracks.listTracks).mockResolvedValue([saved(), { ...saved(), id: 8, short_id: 8, title: 'Other song' }])
  store.historyLoaded = false
  const history = store.loadHistory(); await settle()
  if (action === 'rename') {
    vi.mocked(tracks.renameTrack).mockResolvedValue({ ...saved(), title: 'Latest title' })
    await store.renameJob(job, 'Latest title')
    expect(store.jobs[0]?.title).toBe('Latest title')
  } else {
    vi.mocked(tracks.deleteTrack).mockResolvedValue(undefined)
    await store.deleteJob(job); expect(store.jobs).toHaveLength(0)
  }
  voice.resolve(idle); await history
  expect(store.jobs.find(item => item.dbId === 8)?.title).toBe('Other song')
  if (action === 'rename') expect(store.jobs.find(item => item.dbId === 7)).toMatchObject({ title: 'Latest title', style: 'folk, acoustic guitar' })
  else expect(store.jobs.find(item => item.dbId === 7)).toBeUndefined()
  expect(store.historyLoaded).toBe(true)
  expect(tracks.listTracks).toHaveBeenCalledTimes(2)
})

it.each(['rename', 'delete'] as const)('leaves a valid pending history snapshot intact after %s fails', async action => {
  const store = useYue2Store(); await store.loadHistory()
  const job = store.jobs[0]; if (!job) throw new Error('Missing saved job')
  const voice = deferred<ApplyStatus>()
  vi.mocked(voices.applyStatus).mockReturnValueOnce(voice.promise)
  const history = store.loadHistory(); await settle()
  if (action === 'rename') {
    vi.mocked(tracks.renameTrack).mockRejectedValue(new Error('offline'))
    await expect(store.renameJob(job, 'Uncommitted title')).rejects.toThrow('offline')
  } else {
    vi.mocked(tracks.deleteTrack).mockRejectedValue(new Error('offline'))
    await expect(store.deleteJob(job)).rejects.toThrow('offline')
  }
  voice.resolve(idle); await history
  expect(store.jobs).toHaveLength(1)
  expect(store.jobs[0]?.title).toBe('Renamed song')
  expect(store.historyLoaded).toBe(true)
})

it('keeps edits owned by the latest history request when an older history request fails', async () => {
  const store = useYue2Store(); await store.loadHistory()
  const job = store.jobs[0]; if (!job) throw new Error('Missing saved job')
  const oldList = deferred<Awaited<ReturnType<typeof tracks.listTracks>>>()
  vi.mocked(tracks.listTracks).mockReturnValueOnce(oldList.promise)
  const oldHistory = store.loadHistory()
  const oldCompletion = expect(oldHistory).resolves.toBeUndefined()
  const voice = deferred<ApplyStatus>()
  vi.mocked(voices.applyStatus).mockReturnValueOnce(voice.promise)
  store.historyLoaded = false
  const currentHistory = store.loadHistory(); await settle()
  vi.mocked(tracks.renameTrack).mockResolvedValue({ ...saved(), title: 'Current title' })
  await store.renameJob(job, 'Current title')
  oldList.reject(new Error('old request failed')); await oldCompletion
  expect(store.historyLoaded).toBe(false)
  voice.resolve(idle); await currentHistory
  expect(store.jobs[0]?.title).toBe('Current title')
  expect(store.historyLoaded).toBe(true)
  expect(tracks.listTracks).toHaveBeenCalledTimes(3)
})

it.each(['rename', 'delete'] as const)('applies successful %s by saved track identity after history replaces its generated card', async action => {
  const store = useYue2Store(); await store.loadHistory()
  const job = store.jobs[0]; if (!job) throw new Error('Missing saved job')
  job.id = 'generation-session-7'
  if (action === 'rename') {
    const request = deferred<Awaited<ReturnType<typeof tracks.renameTrack>>>()
    vi.mocked(tracks.renameTrack).mockReturnValueOnce(request.promise)
    const rename = store.renameJob(job, 'Latest title')
    await store.loadHistory()
    request.resolve({ ...saved(), title: 'Latest title' }); await rename
    expect(store.jobs[0]?.title).toBe('Latest title')
  } else {
    const request = deferred<void>()
    vi.mocked(tracks.deleteTrack).mockReturnValueOnce(request.promise)
    const deletion = store.deleteJob(job)
    await store.loadHistory()
    request.resolve(); await deletion
    expect(store.jobs).toHaveLength(0)
  }
})

it('discards completed history reconciliation so a subsequent load remains authoritative', async () => {
  const store = useYue2Store(); await store.loadHistory()
  const job = store.jobs[0]; if (!job) throw new Error('Missing saved job')
  const voice = deferred<ApplyStatus>()
  vi.mocked(voices.applyStatus).mockReturnValueOnce(voice.promise)
  const history = store.loadHistory(); await settle()
  vi.mocked(tracks.renameTrack).mockResolvedValue({ ...saved(), title: 'Local title' })
  await store.renameJob(job, 'Local title')
  voice.resolve(idle); await history
  expect(store.jobs[0]?.title).toBe('Local title')
  vi.mocked(tracks.listTracks).mockResolvedValue([{ ...saved(), title: 'Later server title' }])
  await store.loadHistory()
  expect(store.jobs[0]?.title).toBe('Later server title')
})
