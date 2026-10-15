// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import * as adapters from '../../api/musicLora'
import * as training from '../../api/stableLora'
import * as tracks from '../../api/tracks'
import { ApiError } from '../../api/http'
import type { MusicAdapter, StableLoraTrainingJob, StableLoraTrainingJobs } from '../../api/generated'
import MusicLoraPage from './MusicLoraPage.vue'
vi.mock('../../api/musicLora', async original => ({ ...await original<typeof import('../../api/musicLora')>(), listMusicAdapters: vi.fn(), importMusicAdapter: vi.fn(), musicLoraCapabilities: vi.fn() }))
vi.mock('../../api/stableLora', () => ({ stableLoraStatus: vi.fn(), listStableLora: vi.fn(), submitStableLora: vi.fn(), cancelStableLora: vi.fn() }))
vi.mock('../../api/tracks', () => ({ listTracks: vi.fn(), uploadTrack: vi.fn() }))
let app: App | undefined
const adapter: MusicAdapter = { id: 'a'.repeat(32), engine: 'stable_audio3', target: 'small-music', name: 'Piano', sha256: 'b'.repeat(64), created_at: 'now', source_job_id: null }
const request = { name: 'Piano', variant: 'small-music', samples: [{ track_id: 42, caption: 'Solo piano' }], max_steps: 1000, rank: 16, learning_rate: 0.0001, seed: 42 } as const
function job(status: StableLoraTrainingJob['status'] = 'running'): StableLoraTrainingJob { return { id: 'c'.repeat(32), status, stage: status === 'done' ? 'done' : 'training', created_at: 'now', request: { ...request, samples: [...request.samples] }, adapter: status === 'done' ? adapter : null, error_code: null } }
async function settle() { for (let index = 0; index < 14; index++) await nextTick() }
async function mount() { const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: MusicLoraPage }, { path: '/ace-step/lora', component: { render: () => null } }, { path: '/settings', component: { render: () => null } }] }); await router.push('/'); const node = document.body.appendChild(document.createElement('div')); app = createApp(MusicLoraPage).use(router).use(i18n); app.mount(node); await settle(); return node }
function button(node: ParentNode, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
async function input(node: ParentNode, name: string, value: string) { const element = node.querySelector(`[name="${name}"]`); if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) throw new Error(`Missing ${name}`); element.value = value; element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); await settle() }
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(adapters.musicLoraCapabilities).mockResolvedValue({ engines: [{ engine: 'ace_step', loading: true, training: true, reason: 'native' }, { engine: 'stable_audio3', loading: true, training: true, reason: 'native' }, { engine: 'yue2', loading: true, training: false, reason: 'inference_only' }, { engine: 'heartmula', loading: false, training: false, reason: 'unsupported_runtime' }] })
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [], history_incomplete: false })
  vi.mocked(adapters.importMusicAdapter).mockResolvedValue(adapter)
  vi.mocked(training.stableLoraStatus).mockResolvedValue({ supported: true, installed: true, small_weights_ready: true, medium_weights_ready: false })
  vi.mocked(training.listStableLora).mockResolvedValue({ jobs: [], history_incomplete: false })
  vi.mocked(training.submitStableLora).mockResolvedValue(job('queued')); vi.mocked(training.cancelStableLora).mockResolvedValue(job('cancelled'))
  vi.mocked(tracks.listTracks).mockResolvedValue([{ id: 42, short_id: 42, model: 'upload', title: 'Piano recording', created_at: 'now', lyrics: '', seed: null, duration_ms: 30000, wall_ms: null, params: {}, filename: 'piano.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null }])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
it('shows actual engine capabilities and links the existing ACE workflow without automatic jobs', async () => {
  const node = await mount()
  expect(node.querySelector('[data-engine="heartmula"]')?.textContent).toContain('no adapter API or trainer')
  expect(node.querySelector('[data-engine="yue2"]')?.textContent).toContain('Loading only')
  expect(node.querySelector('a[href="/ace-step/lora"]')).not.toBeNull()
  expect(node.textContent).toContain('PEFT checkpoints with a companion JSON are not supported')
  expect(training.submitStableLora).not.toHaveBeenCalled(); expect(adapters.importMusicAdapter).not.toHaveBeenCalled()
})
it('imports only through a chosen file and matching native target and offers export', async () => {
  const node = await mount(); await input(node, 'import-name', 'Piano')
  expect(button(node, 'Import adapter').disabled).toBe(true)
  const file = new File(['native checkpoint'], 'piano.safetensors'), picker = node.querySelector('input[type="file"]')
  if (!(picker instanceof HTMLInputElement)) throw new Error('Missing adapter upload')
  const transfer = new DataTransfer(); transfer.items.add(file); picker.files = transfer.files; picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  button(node, 'Import adapter').click(); await settle()
  expect(adapters.importMusicAdapter).toHaveBeenCalledWith(file, { engine: 'stable_audio3', target: 'small-music', name: 'Piano' }, expect.any(AbortSignal))
  expect(node.querySelector(`a[href="/api/music-lora/adapters/${adapter.id}/file"]`)).not.toBeNull()
})
it('selects whole library tracks with required captions and submits a bounded fresh training job', async () => {
  const node = await mount(); await input(node, 'training-name', 'Piano')
  button(node, 'Add or upload a library track').click(); await settle()
  button(document, i18n.global.t('library.fullMix')).click(); await settle()
  expect(button(node, 'Start training').disabled).toBe(true)
  await input(node, 'caption-42', 'Solo piano')
  const form = node.querySelector('form[data-training]'); if (!(form instanceof HTMLFormElement)) throw new Error('Missing training form')
  expect(form.checkValidity()).toBe(true)
  button(node, 'Start training').click(); await settle()
  expect(training.submitStableLora).toHaveBeenCalledExactlyOnceWith({ ...request, samples: [...request.samples] }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Queued'); expect(node.textContent).not.toMatch(/\d+%/)
})
it('requires weights for the selected training variant and enforces sample removal', async () => {
  const node = await mount()
  await input(node, 'training-variant', 'medium')
  expect(node.textContent).toContain('Training weights for the selected base model are missing')
  expect(button(node, 'Start training').disabled).toBe(true)
  await input(node, 'training-variant', 'small-music')
  button(node, 'Add or upload a library track').click(); await settle(); button(document, i18n.global.t('library.fullMix')).click(); await settle()
  expect(node.textContent).toContain('Piano recording')
  button(node, 'Remove track').click(); await settle()
  expect(node.querySelector('[name="caption-42"]')).toBeNull()
})
it('restores durable history, cancels running work and stops polling terminal jobs', async () => {
  vi.useFakeTimers(); vi.mocked(training.listStableLora).mockResolvedValue({ jobs: [job()], history_incomplete: true })
  const node = await mount(); expect(node.textContent).toContain('Some saved history could not be read')
  button(node, 'Cancel training').click(); await settle()
  expect(training.cancelStableLora).toHaveBeenCalledExactlyOnceWith(job().id, expect.any(AbortSignal))
  expect(node.textContent).toContain('Cancelled')
  await vi.advanceTimersByTimeAsync(5000); expect(training.listStableLora).toHaveBeenCalledTimes(1)
})
it('continues polling after transient errors and exposes a completed checkpoint export', async () => {
  vi.useFakeTimers(); vi.mocked(training.listStableLora).mockResolvedValueOnce({ jobs: [job()], history_incomplete: false }).mockRejectedValueOnce(new Error('/private/token')).mockResolvedValue({ jobs: [job('done')], history_incomplete: false })
  const node = await mount(); await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(node.textContent).toContain('Could not load music adapters'); expect(node.textContent).not.toContain('/private/token')
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(node.textContent).toContain('Adapter saved'); expect(node.querySelector(`a[href="/api/music-lora/adapters/${adapter.id}/file"]`)).not.toBeNull()
})
it('aborts pending polling after teardown without cancelling backend training', async () => {
  vi.useFakeTimers(); let finish: ((value: StableLoraTrainingJobs) => void) | undefined
  vi.mocked(training.listStableLora).mockResolvedValueOnce({ jobs: [job()], history_incomplete: false }).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  await mount(); await vi.advanceTimersByTimeAsync(2500)
  const signal = vi.mocked(training.listStableLora).mock.calls.at(-1)?.[0]
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  finish?.({ jobs: [job()], history_incomplete: false }); await settle(); await vi.advanceTimersByTimeAsync(10000)
  expect(training.listStableLora).toHaveBeenCalledTimes(2); expect(training.cancelStableLora).not.toHaveBeenCalled()
})
it('maps known adapter format errors and never renders internal exception details', async () => {
  vi.mocked(adapters.listMusicAdapters).mockRejectedValue(new Error('/private/token'))
  const node = await mount(); expect(node.textContent).not.toContain('/private/token')
  vi.mocked(adapters.importMusicAdapter).mockRejectedValue(new ApiError('music_lora_format_invalid', 422))
  await input(node, 'import-name', 'Piano')
  const picker = node.querySelector('input[type="file"]'); if (!(picker instanceof HTMLInputElement)) throw new Error('Missing upload')
  const transfer = new DataTransfer(); transfer.items.add(new File(['native checkpoint'], 'piano.safetensors')); picker.files = transfer.files; picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  button(node, 'Import adapter').click(); await settle()
  expect(node.textContent).toContain('not a supported native adapter'); expect(node.textContent).not.toContain('music_lora_format_invalid')
})

it('explains an older custom YuE runtime that requires an upgrade', async () => {
  vi.mocked(adapters.musicLoraCapabilities).mockResolvedValue({ engines: [{ engine: 'yue2', loading: false, training: false, reason: 'upgrade_required' }] })
  const node = await mount()
  expect(node.querySelector('[data-engine="yue2"]')?.textContent).toContain('Update the native engine to use this adapter format')
  expect(node.textContent).toContain('first 600 seconds of each track')
  expect(node.textContent).toContain('latents and checkpoints remain on disk')
})
