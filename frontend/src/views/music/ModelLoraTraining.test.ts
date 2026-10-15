// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import * as api from '../../api/musicTraining'
import * as tracks from '../../api/tracks'
import * as datasets from '../../api/loraDataset'
import { parseLoraDatasetOperationResponse, type MusicLoraTrainingJob } from '../../api/generated'
import { pendingMusicAdapter } from '../../composables/generationDrafts'
import ModelLoraTraining from './ModelLoraTraining.vue'
vi.mock('../../api/musicTraining', () => ({ musicTrainingStatus: vi.fn(), listMusicTraining: vi.fn(), submitMusicTraining: vi.fn(), createMusicTrainingPackage: vi.fn(), musicTrainingPackageUrl: (id: string) => `/api/music-lora/training/jobs/${id}/package`, cancelMusicTraining: vi.fn() }))
vi.mock('../../api/modules', () => ({ listModuleJobs: vi.fn(async () => []) }))
vi.mock('../../api/tracks', () => ({ listTracks: vi.fn(), uploadTrack: vi.fn() }))
vi.mock('../../api/loraDataset', () => ({ listLoraDatasets: vi.fn(), readSavedLoraDataset: vi.fn() }))
let app: App | undefined
const request = { engine: 'yue2', name: 'Warm strings', samples: [{ track_id: 42, caption: 'Warm strings', lyrics: '', instrumental: false }], max_steps: 1000, rank: 16, learning_rate: 0.0001, seed: 42, segment_seconds: 12 } as const
const job = (status: MusicLoraTrainingJob['status'] = 'running'): MusicLoraTrainingJob => ({ id: 'c'.repeat(32), status, created_at: '2026-10-15T18:00:00Z', request: { ...request, samples: [...request.samples] }, progress: { stage: 'training', step: 2, total_steps: 1000, loss: 0.42 }, adapter: null, error_code: null })
async function settle() { for (let i = 0; i < 16; i++) await nextTick() }
async function mount(engine: 'yue2' | 'heartmula' = 'yue2', onRegistered = vi.fn()) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ModelLoraTraining }, { path: '/settings', component: { render: () => null } }, { path: '/music/:engine', component: { render: () => null } }] }); await router.push('/')
  const node = document.body.appendChild(document.createElement('div')); app = createApp(ModelLoraTraining, { engine, onRegistered }).use(createPinia()).use(router).use(i18n); app.mount(node); await settle(); return node
}
function button(node: ParentNode, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
async function input(node: ParentNode, name: string, value: string) { const field = node.querySelector(`[name="${name}"]`); if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) throw new Error(`Missing ${name}`); field.value = value; field.dispatchEvent(new Event('input', { bubbles: true })); await settle() }
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.musicTrainingStatus).mockResolvedValue({ engines: [{ engine: 'yue2', supported: true, installed: true, weights_ready: true, device: 'mps', validation: 'experimental' }, { engine: 'heartmula', supported: true, installed: true, weights_ready: true, device: 'mps', validation: 'experimental' }] })
  vi.mocked(api.listMusicTraining).mockResolvedValue({ jobs: [], history_incomplete: false }); vi.mocked(api.submitMusicTraining).mockResolvedValue(job('queued')); vi.mocked(api.cancelMusicTraining).mockResolvedValue(job('cancelled'))
  vi.mocked(api.createMusicTrainingPackage).mockResolvedValue({ ...job('queued'), kind: 'nvidia-package', package_sha256: null })
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [], issues: [] })
  vi.mocked(tracks.listTracks).mockResolvedValue([{ id: 42, short_id: 42, model: 'upload', title: 'String recording', created_at: 'now', lyrics: '', seed: null, duration_ms: 12000, wall_ms: null, params: {}, filename: 'strings.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null }])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); pendingMusicAdapter.value = null; vi.useRealTimers() })
it.each(['heartmula', 'yue2'] as const)('explains the %s training options without starting work', async engine => {
  const node = await mount(engine)
  const info = node.querySelector('[aria-label="About training options"]')
  if (!(info instanceof HTMLButtonElement)) throw new Error('Missing training info button')
  info.click(); await settle()
  const dialog = document.querySelector('[role="dialog"]')
  expect(dialog?.textContent).toContain('Excerpt length in seconds')
  expect(dialog?.textContent).toContain('Learning rate')
  expect(dialog?.textContent).toContain('Optional NVIDIA fallback')
  expect(dialog?.textContent).toContain(engine === 'heartmula' ? 'exact words sung in it' : 'composition planner stays unchanged')
  expect(api.submitMusicTraining).not.toHaveBeenCalled()
  expect(api.createMusicTrainingPackage).not.toHaveBeenCalled()
})
it('teaches the acoustic scope and submits reviewed tracks through the owned training API', async () => {
  const node = await mount()
  expect(node.textContent).toContain('composition planner stays unchanged')
  expect(api.submitMusicTraining).not.toHaveBeenCalled()
  await input(node, 'model-training-name', 'Warm strings')
  button(node, 'Add or upload a library track').click(); await settle(); button(document, i18n.global.t('library.addToTraining')).click(); await settle()
  expect(button(node, 'Start training').disabled).toBe(true)
  await input(node, 'model-caption-42', 'Warm strings')
  button(node, 'Start training').click(); await settle()
  expect(api.submitMusicTraining).toHaveBeenCalledExactlyOnceWith({ ...request, samples: [...request.samples] }, expect.any(AbortSignal))
})
it('restores progress after reload and cancels only when requested', async () => {
  vi.mocked(api.listMusicTraining).mockResolvedValue({ jobs: [job()], history_incomplete: false })
  const node = await mount()
  expect(node.textContent).toContain('2 / 1000')
  expect(node.textContent).toContain('0.4200')
  button(node, 'Cancel training').click(); await settle()
  expect(api.cancelMusicTraining).toHaveBeenCalledWith(job().id, expect.any(AbortSignal))
})
it('keeps vocal excerpts and matching lyrics explicit on HeartMuLa', async () => {
  const node = await mount('heartmula')
  expect(node.textContent).toContain('matching lyrics')
  expect(node.textContent).toContain('12 seconds')
  expect(node.textContent).not.toContain('composition planner')
})
it('reconnects after an uncertain submission without resubmitting on its own', async () => {
  vi.useFakeTimers()
  const node = await mount()
  await input(node, 'model-training-name', 'Warm strings')
  button(node, 'Add or upload a library track').click(); await settle(); button(document, i18n.global.t('library.addToTraining')).click(); await settle(); await input(node, 'model-caption-42', 'Warm strings')
  vi.mocked(api.submitMusicTraining).mockRejectedValueOnce(new Error('/private/token'))
  vi.mocked(api.listMusicTraining).mockResolvedValue({ jobs: [job()], history_incomplete: false })
  button(node, 'Start training').click(); await settle(); await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(api.submitMusicTraining).toHaveBeenCalledTimes(1)
  expect(node.textContent).toContain('2 / 1000')
  expect(node.textContent).not.toContain('/private/token')
})
it('creates a downloadable NVIDIA package from reviewed recordings without local training setup', async () => {
  vi.mocked(api.musicTrainingStatus).mockResolvedValue({ engines: [{ engine: 'heartmula', supported: false, installed: false, weights_ready: false, device: null, validation: 'experimental' }] })
  const dataset = { id: 'a'.repeat(32), name: 'Reviewed vocals', dataset_path: 'datasets/vocals/dataset.json', audio_dir: 'datasets/vocals', sample_count: 1, labeled_count: 1, revision: 7, available: true, trashed: false }
  const snapshot = parseLoraDatasetOperationResponse({ dataset, samples: [{ id: 'clip', filename: 'vocals.wav', caption: 'Warm piano', labeled: true, is_instrumental: false, raw_lyrics: 'Hello there' }], metadata: {} })
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [dataset], issues: [] })
  vi.mocked(datasets.readSavedLoraDataset).mockResolvedValue(snapshot)
  const node = await mount('heartmula')
  const fallback = node.querySelector('details[data-nvidia-fallback]')
  expect(fallback).toBeInstanceOf(HTMLDetailsElement)
  expect(fallback?.hasAttribute('open')).toBe(false)
  expect(node.textContent).toContain('CUDA training has not been tested')
  expect(node.textContent).toContain('Nothing is uploaded automatically')
  expect(button(node, 'Create NVIDIA package').disabled).toBe(true)
  await input(node, 'model-training-name', 'Warm strings')
  button(node, 'Add a saved dataset').click(); await settle(); button(node, 'Add up to 1 recordings').click(); await settle()
  expect(button(node, 'Start training').disabled).toBe(true)
  expect(button(node, 'Create NVIDIA package').disabled).toBe(false)
  button(node, 'Create NVIDIA package').click(); await settle()
  expect(api.createMusicTrainingPackage).toHaveBeenCalledExactlyOnceWith({ ...request, engine: 'heartmula', samples: [{ dataset: { dataset_id: dataset.id, revision: 7, sample_id: 'clip' }, caption: 'Warm piano', lyrics: 'Hello there', instrumental: false }] }, expect.any(AbortSignal))
  expect(api.submitMusicTraining).not.toHaveBeenCalled()
})
it('restores package history with an explicit download and never announces adapter registration', async () => {
  const completed = { ...job('done'), kind: 'nvidia-package', package_sha256: 'f'.repeat(64) } satisfies MusicLoraTrainingJob
  vi.mocked(api.listMusicTraining).mockResolvedValue({ jobs: [completed], history_incomplete: false })
  const registered = vi.fn()
  const node = await mount('yue2', registered)
  expect(node.textContent).toContain('NVIDIA package ready')
  const download = [...node.querySelectorAll('a')].find(link => link.textContent === 'Download NVIDIA package')
  expect(download?.getAttribute('href')).toBe(`/api/music-lora/training/jobs/${completed.id}/package`)
  expect(download?.hasAttribute('download')).toBe(true)
  expect(registered).not.toHaveBeenCalled()
  expect(node.textContent).not.toContain('Preview with adapter')
  expect(pendingMusicAdapter.value).toBeNull()
})
it.each(['unknown history', 'active job'] as const)('keeps NVIDIA packaging disabled for %s', async condition => {
  if (condition === 'unknown history') vi.mocked(api.listMusicTraining).mockRejectedValue(new Error('offline'))
  else vi.mocked(api.listMusicTraining).mockResolvedValue({ jobs: [{ ...job(), kind: 'nvidia-package' }], history_incomplete: false })
  const node = await mount()
  await input(node, 'model-training-name', 'Warm strings')
  button(node, 'Add or upload a library track').click(); await settle(); button(document, i18n.global.t('library.addToTraining')).click(); await settle()
  await input(node, 'model-caption-42', 'Warm strings')
  expect(button(node, 'Create NVIDIA package').disabled).toBe(true)
  expect(api.createMusicTrainingPackage).not.toHaveBeenCalled()
})
it('clears mismatching lyrics when an instrumental clip is selected and submits the review', async () => {
  const node = await mount('heartmula')
  await input(node, 'model-training-name', 'Warm strings')
  button(node, 'Add or upload a library track').click(); await settle(); button(document, i18n.global.t('library.addToTraining')).click(); await settle()
  await input(node, 'model-caption-42', 'Warm piano')
  await input(node, 'model-lyrics-42', 'Previous lyrics')
  const instrumental = node.querySelector('input[type="checkbox"]')
  if (!(instrumental instanceof HTMLInputElement)) throw new Error('Missing instrumental selection')
  instrumental.click(); await settle()
  expect(node.querySelector('[name="model-lyrics-42"]')).toBeNull()
  button(node, 'Start training').click(); await settle()
  expect(api.submitMusicTraining).toHaveBeenCalledExactlyOnceWith({ ...request, engine: 'heartmula', samples: [{ track_id: 42, caption: 'Warm piano', lyrics: '', instrumental: true }] }, expect.any(AbortSignal))
})
it('opens adapter and base previews without starting generation', async () => {
  const completed: MusicLoraTrainingJob = { ...job('done'), adapter: { id: 'a'.repeat(32), engine: 'yue2', target: 'nar', name: 'Warm strings', sha256: 'f'.repeat(64), created_at: '2026-10-15', source_job_id: job().id } }
  vi.mocked(api.listMusicTraining).mockResolvedValue({ jobs: [completed], history_incomplete: false })
  const node = await mount()
  const links = [...node.querySelectorAll('a')]
  links.find(link => link.textContent === 'Preview with adapter')?.click(); await settle()
  expect(pendingMusicAdapter.value).toEqual({ engine: 'yue2', target: 'nar', selection: { id: 'a'.repeat(32), strength: 1 } })
  links.find(link => link.textContent === 'Compare base model')?.click(); await settle()
  expect(pendingMusicAdapter.value).toEqual({ engine: 'yue2', target: 'nar', selection: null })
  expect(api.submitMusicTraining).not.toHaveBeenCalled()
})
it('compares the HeartMuLa base at zero adapter strength in the same corrected runtime', async () => {
  const completed: MusicLoraTrainingJob = { ...job('done'), request: { ...job().request, engine: 'heartmula' }, adapter: { id: 'a'.repeat(32), engine: 'heartmula', target: 'heartmula-3b', name: 'Warm voice', sha256: 'f'.repeat(64), created_at: '2026-10-15', source_job_id: job().id } }
  vi.mocked(api.listMusicTraining).mockResolvedValue({ jobs: [completed], history_incomplete: false })
  const node = await mount('heartmula')
  const compare = [...node.querySelectorAll('a')].find(link => link.textContent === 'Compare base model')
  if (!compare) throw new Error('Missing HeartMuLa base comparison')
  compare.click(); await settle()
  expect(pendingMusicAdapter.value).toEqual({ engine: 'heartmula', target: 'heartmula-3b', selection: { id: 'a'.repeat(32), strength: 0 } })
  expect(node.textContent).toContain('strength 0 in the same corrected runtime')
  expect(api.submitMusicTraining).not.toHaveBeenCalled()
})
it('sends a validated package request and constrains package download IDs', async () => {
  const actual = await vi.importActual<typeof import('../../api/musicTraining')>('../../api/musicTraining')
  const queued = { ...job('queued'), kind: 'nvidia-package', package_sha256: null } satisfies MusicLoraTrainingJob
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(queued)))
  const signal = new AbortController().signal
  const body = { ...request, samples: [...request.samples] }
  try {
    await expect(actual.createMusicTrainingPackage(body, signal)).resolves.toMatchObject(queued)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music-lora/training/packages', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    expect(() => actual.createMusicTrainingPackage({ ...body, max_steps: 0 })).toThrow()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(actual.musicTrainingPackageUrl(queued.id)).toBe(`/api/music-lora/training/jobs/${queued.id}/package`)
    expect(() => actual.musicTrainingPackageUrl('../outside')).toThrow('Invalid music training job ID')
  } finally { fetch.mockRestore() }
})
