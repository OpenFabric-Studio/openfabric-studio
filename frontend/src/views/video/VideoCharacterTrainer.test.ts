// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import VideoCharacterTrainer from './VideoCharacterTrainer.vue'
import * as api from '../../api/videos'
import type { VideoCharacterTrainingResponse, VideoCharacterTrainingJob } from '../../api/contracts'
import { videoProjectFixture } from './videoFixtures'
import { i18n } from '../../i18n'

vi.mock('../../api/videos', async original => ({ ...await original<typeof import('../../api/videos')>(), characterTrainerStatus: vi.fn(), listCharacterTraining: vi.fn(), startCharacterTraining: vi.fn(), cancelCharacterTraining: vi.fn(), saveCharacterTrainer: vi.fn() }))
let app: App | undefined
const job: VideoCharacterTrainingJob = { id: 'a'.repeat(32), name: 'Train', status: 'running', consent_confirmed: true, photo_count: 3, clip_count: 0, created_at: 'now', updated_at: 'now' }
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>(release => { resolve = release })
  return { promise, resolve }
}
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount() {
  app = createApp({ render: () => h(VideoCharacterTrainer, { project: videoProjectFixture(undefined, null), readOnly: false }) }).use(i18n)
  const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle(); return node
}
beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks(); vi.mocked(api.characterTrainerStatus).mockResolvedValue({ configured: false, source: 'none' }); vi.mocked(api.listCharacterTraining).mockResolvedValue({ jobs: [] }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })

it('does not resurrect trainer polling when initial requests resolve after unmount', async () => {
  const request = deferred<VideoCharacterTrainingResponse>(); vi.mocked(api.listCharacterTraining).mockReturnValue(request.promise)
  await mount(); app?.unmount(); app = undefined
  const signal = vi.mocked(api.listCharacterTraining).mock.calls[0]?.[0]
  request.resolve({ jobs: [job] }); await settle(); await vi.advanceTimersByTimeAsync(15_000)
  expect(api.listCharacterTraining).toHaveBeenCalledOnce()
  expect(signal?.aborted).toBe(true)
})

it('waits for the previous poll and ignores its response after unmount', async () => {
  const request = deferred<VideoCharacterTrainingResponse>()
  vi.mocked(api.listCharacterTraining).mockResolvedValueOnce({ jobs: [job] }).mockReturnValue(request.promise)
  await mount(); await vi.advanceTimersByTimeAsync(9000)
  expect(api.listCharacterTraining).toHaveBeenCalledTimes(2)
  app?.unmount(); app = undefined
  request.resolve({ jobs: [job] }); await settle(); await vi.advanceTimersByTimeAsync(9000)
  expect(api.listCharacterTraining).toHaveBeenCalledTimes(2)
})

async function submitTraining(node: HTMLElement) {
  const photos = node.querySelector('[data-trainer-photos]'), consent = node.querySelector('[data-trainer-consent]'), name = node.querySelector('input[maxlength="80"]')
  if (!(photos instanceof HTMLInputElement) || !(consent instanceof HTMLInputElement) || !(name instanceof HTMLInputElement)) throw new Error('Missing trainer input')
  Object.defineProperty(photos, 'files', { value: [1, 2, 3].map(index => new File(['photo'], `${index}.png`, { type: 'image/png' })), configurable: true })
  name.value = 'Character'; name.dispatchEvent(new Event('input')); consent.checked = true; consent.dispatchEvent(new Event('change')); await settle()
  const form = photos.closest('form')
  if (!form) throw new Error('Missing trainer form')
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle(); return form
}

it('owns one training submission and does not restart polling from its late completion', async () => {
  const request = deferred<VideoCharacterTrainingJob>(); vi.mocked(api.startCharacterTraining).mockReturnValue(request.promise)
  const node = await mount(), form = await submitTraining(node)
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
  expect(api.startCharacterTraining).toHaveBeenCalledOnce()
  app?.unmount(); app = undefined
  expect(vi.mocked(api.startCharacterTraining).mock.calls[0]?.[1]?.aborted).toBe(true)
  request.resolve(job); await settle(); await vi.advanceTimersByTimeAsync(9000)
  expect(api.listCharacterTraining).toHaveBeenCalledOnce()
})

it('observes an accepted training job until completion', async () => {
  vi.mocked(api.startCharacterTraining).mockResolvedValue(job)
  vi.mocked(api.listCharacterTraining).mockResolvedValueOnce({ jobs: [] }).mockResolvedValue({ jobs: [{ ...job, status: 'completed' }] })
  const node = await mount(); await submitTraining(node); await vi.advanceTimersByTimeAsync(3000)
  expect(node.textContent).toContain('Train')
  expect(api.listCharacterTraining).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(9000)
  expect(api.listCharacterTraining).toHaveBeenCalledTimes(2)
})

it('shows effective recorded recipe and separates completion from likeness review', async () => {
  vi.mocked(api.listCharacterTraining).mockResolvedValue({ jobs: [{ ...job, status: 'completed', adapter_ready: true,
    provenance: { engine_commit: 'b'.repeat(40), base_revision: 'c'.repeat(40), dataset_sha256: 'd'.repeat(64), settings_sha256: 'e'.repeat(64),
      settings: { base_profile: 'ltx23', steps: 120, rank: 16 }, artifacts: [], comparison_prompts: [],
      recipe: { width: 960, height: 544, frames: 97, frame_rate: 24, learning_rate: 0.0002, memory_mode: 'low_ram' } } }] })
  const node = await mount()
  expect(node.textContent).toContain('Effective built-in recipe')
  expect(node.textContent).toContain('120 steps · rank 16')
  expect(node.textContent).toContain('960×544')
  expect(node.textContent).toContain('Likeness has not been evaluated')
  expect(node.textContent).toContain('does not establish likeness')
})
