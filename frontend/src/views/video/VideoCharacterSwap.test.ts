// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { CharacterSwapJob, OpenRouterQuote, VideoSceneCut } from '../../api/contracts'
import { parseCharacterSwapJob } from '../../api/contracts'
import * as api from '../../api/characterSwaps'
import VideoCharacterSwap from './VideoCharacterSwap.vue'

vi.mock('../../api/characterSwaps', () => ({
  CHARACTER_SWAP_MAX_VIDEO_BYTES: 100 * 1024 * 1024, characterSwapReadiness: vi.fn(), listCharacterSwaps: vi.fn(), prepareCharacterSwap: vi.fn(), prepareCharacterSwapFromScene: vi.fn(),
  getCharacterSwap: vi.fn(), quoteCharacterSwap: vi.fn(), submitCharacterSwap: vi.fn(), resumeCharacterSwap: vi.fn(), stopCharacterSwap: vi.fn(),
}))
let app: App | undefined
function job(status: CharacterSwapJob['status'] = 'prepared', id = 'a'.repeat(32), stage: CharacterSwapJob['stage'] = 'frame'): CharacterSwapJob {
  return { id, revision: 1, name: 'New character', status, phase: '', error_code: '',
    created_at: '2026-10-11T10:00:00Z', updated_at: '2026-10-11T10:00:00Z', start_sec: 0, seconds: 2,
    source_name: 'source.mp4', image_name: 'character.png', source_duration_sec: 10, width: 640, height: 480,
    frames: 48, has_audio: true, estimated_usd: 0.24, quote_expires_at: '2026-10-11T12:00:00Z', seed: 42,
    source_url: `/api/videos/character-swaps/${id}/source`, clip_url: `/api/videos/character-swaps/${id}/clip`,
    image_url: `/api/videos/character-swaps/${id}/image`, output_url: '', output_sha256: '', remote_id: null,
    provider: 'openrouter', model_id: stage === 'frame' ? 'bytedance-seed/seedream-4.5' : 'runway/aleph-2', price_checked_at: '2026-10-11',
    stage, quote: null, source_frame_url: `/api/videos/character-swaps/${id}/frame-original`,
    edited_frame_url: stage === 'video' ? `/api/videos/character-swaps/${id}/frame-edited` : '',
    frame_receipt_id: stage === 'video' ? 'f'.repeat(32) : null, video_receipt_id: null, resume_available: false }
}
function quotedJob(value = job()): CharacterSwapJob {
  const quote: OpenRouterQuote = { id: (value.stage === 'video' ? 'f' : 'c').repeat(32), kind: value.stage === 'video' ? 'video' : 'image', model_id: value.model_id ?? '',
    model_fingerprint: 'd'.repeat(64), request_fingerprint: 'e'.repeat(64), estimated_usd: value.stage === 'video' ? .56 : .04,
    expires_at: Date.parse('2026-10-11T12:00:00Z') / 1000, transfers: value.stage === 'video' ? ['prompt', 'reference_video', 'reference_image'] : ['prompt', 'source_frame', 'reference_image'] }
  return { ...value, quote }
}
it('rejects external preview and download links at the client boundary', () => {
  expect(parseCharacterSwapJob(job()).id).toBe(job().id)
  for (const field of ['source_url', 'clip_url', 'image_url', 'output_url', 'source_frame_url', 'edited_frame_url']) {
    expect(() => parseCharacterSwapJob({ ...job(), [field]: 'https://external.example/media' })).toThrow()
  }
})
async function settle() { for (let index = 0; index < 10; index++) await nextTick() }
async function mount(chosen?: VideoSceneCut) {
  const scene = ref<VideoSceneCut | null>(chosen ?? null)
  app = createApp({ render: () => h(VideoCharacterSwap, { chosenScene: scene.value }) }).use(i18n)
  app.use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => h('div') } }, { path: '/settings', component: { render: () => h('div') } }] }))
  app.mount(document.body.appendChild(document.createElement('div')))
  await settle()
  return scene
}
function button(selector: string): HTMLButtonElement {
  const value = document.querySelector(selector)
  if (!(value instanceof HTMLButtonElement)) throw new Error(`Missing button ${selector}`)
  return value
}
function confirm() {
  for (const box of document.querySelectorAll<HTMLInputElement>('[data-swap-review] input[type=checkbox]')) {
    box.checked = true; box.dispatchEvent(new Event('change', { bubbles: true }))
  }
}
function upload(selector: string, file: File) {
  const input = document.querySelector(selector)
  if (!(input instanceof HTMLInputElement)) throw new Error(`Missing input ${selector}`)
  const transfer = new DataTransfer()
  transfer.items.add(file)
  input.files = transfer.files
  input.dispatchEvent(new Event('change', { bubbles: true }))
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-11T10:00:00Z')); vi.resetAllMocks()
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: true, ffmpeg_ready: true })
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job()] })
  vi.mocked(api.prepareCharacterSwap).mockResolvedValue(job())
  vi.mocked(api.prepareCharacterSwapFromScene).mockResolvedValue(job())
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(quotedJob())
  vi.mocked(api.submitCharacterSwap).mockResolvedValue({ ...job('queued'), remote_id: 'remote-1' })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })

it('loads saved previews and requires an explicit quote plus separate approvals before one paid frame submission', async () => {
  await mount()
  expect(document.querySelector('video')?.getAttribute('src')).toBe(job().clip_url)
  expect(document.querySelector('[data-swap-source-frame]')?.getAttribute('src')).toBe(job().source_frame_url)
  expect(document.querySelector('[data-swap-reference]')?.getAttribute('src')).toBe(job().image_url)
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  button('[data-swap-quote]').click(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect(document.querySelectorAll('[data-swap-review] input[type=checkbox]')).toHaveLength(2)
  const first = document.querySelector<HTMLInputElement>('[data-swap-review] input[type=checkbox]')
  if (!first) throw new Error('Missing confirmation')
  first.checked = true; first.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  confirm(); await settle()
  button('[data-swap-submit]').click(); button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).toHaveBeenCalledExactlyOnceWith(job().id,
    { revision: 1, quote_id: quotedJob().quote?.id, transfers_confirmed: true, consent_confirmed: true }, expect.any(AbortSignal))
  expect(document.querySelector('[data-swap-submit]')).toBeNull()
})
it.each(['mp4', 'mkv'])('prepares local %s input files without submitting or sending them to the provider', async extension => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [] })
  await mount()
  const video = new File(['video'], `source.${extension}`, { type: extension === 'mkv' ? 'video/x-matroska' : 'video/mp4' })
  const image = new File(['image'], 'character.png', { type: 'image/png' })
  upload('[data-swap-video]', video); upload('[data-swap-image]', image)
  await settle()
  button('[data-swap-prepare]').click(); await settle()
  expect(api.prepareCharacterSwap).toHaveBeenCalledExactlyOnceWith({ video, image, name: 'Character swap', startSec: 0, seconds: 2 }, expect.any(AbortSignal))
  expect(document.querySelector<HTMLInputElement>('[data-swap-video]')?.accept.split(',')).toContain(`.${extension}`)
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('rejects invalid clip durations before preparing a job', async () => {
  await mount()
  upload('[data-swap-video]', new File(['video'], 'source.mp4', { type: 'video/mp4' }))
  upload('[data-swap-image]', new File(['image'], 'character.png', { type: 'image/png' }))
  const duration = document.querySelector('[data-swap-seconds]')
  if (!(duration instanceof HTMLInputElement)) throw new Error('Missing duration')
  duration.value = '1'; duration.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(button('[data-swap-prepare]').disabled).toBe(true)
  expect(api.prepareCharacterSwap).not.toHaveBeenCalled()
})
it('does not submit expired estimates', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob()] })
  await mount(); confirm(); await settle()
  await vi.advanceTimersByTimeAsync(2 * 60 * 60 * 1000)
  expect(button('[data-swap-submit]').disabled).toBe(true)
  button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(document.body.textContent).toContain('estimate expired')
  button('[data-swap-quote]').click(); await settle()
  expect(api.quoteCharacterSwap).toHaveBeenCalledTimes(1)
  expect(api.prepareCharacterSwap).not.toHaveBeenCalled()
})
it('requires new approval after refreshing a saved estimate', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob()] })
  await mount(); confirm(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(false)
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...quotedJob(), revision: 2 }] })
  const refresh = [...document.querySelectorAll('button')].find(value => value.textContent === 'Refresh saved jobs')
  if (!refresh) throw new Error('Missing refresh')
  refresh.click(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('blocks a second paid submission after an unconfirmed request failure', async () => {
  vi.mocked(api.submitCharacterSwap).mockRejectedValue(new Error('Connection lost'))
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob()] })
  await mount(); confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect(document.body.textContent).toContain('action could not be confirmed')
  button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).toHaveBeenCalledTimes(1)
  expect(document.body.textContent).not.toContain('Connection lost')
})
it('requires both readiness checks before submitting a prepared job', async () => {
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: true, ffmpeg_ready: false })
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob()] })
  await mount(); confirm(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect(document.body.textContent).toContain('FFmpeg is required')
})
it('never offers retry or resume when the paid submission outcome is unknown', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job('submission_unknown')] })
  await mount()
  expect(document.querySelector('[data-swap-submit]')).toBeNull()
  expect(document.querySelector('[data-swap-resume]')).toBeNull()
  expect(document.body.textContent).toContain('OpenRouter activity')
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('resumes only a known remote job and explains that stopping tracking cannot guarantee a refund', async () => {
  const existing = { ...job('stopped', 'a'.repeat(32), 'video'), remote_id: 'remote-1', resume_available: true }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.resumeCharacterSwap).mockResolvedValue({ ...existing, status: 'running' })
  await mount(); button('[data-swap-resume]').click(); await settle()
  expect(api.resumeCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id, { revision: 1 }, expect.any(AbortSignal))
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(button('[data-swap-stop]').textContent).toContain('Stop tracking')
  expect(document.body.textContent).toContain('refund')
})
it('blocks resume while a worker has not finished cleanup', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job('stopped', 'a'.repeat(32), 'video'), remote_id: 'remote-1', resume_available: true, error_code: 'worker_unresolved' }] })
  await mount()
  expect(document.querySelector('[data-swap-resume]')).toBeNull()
  expect(document.body.textContent).toContain('Restart the backend')
  expect(api.resumeCharacterSwap).not.toHaveBeenCalled()
})
it('shows local preparation progress even when a saved job was selected', async () => {
  let release: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.prepareCharacterSwap).mockReturnValue(new Promise(resolve => { release = resolve }))
  await mount()
  upload('[data-swap-video]', new File(['video'], 'source.mp4', { type: 'video/mp4' }))
  upload('[data-swap-image]', new File(['image'], 'character.png', { type: 'image/png' }))
  await settle(); button('[data-swap-prepare]').click(); await settle()
  expect(document.body.textContent).toContain('Preparing the selected clip locally')
  release(job()); await settle()
  expect(document.body.textContent).not.toContain('Preparing the selected clip locally')
})
it('shows source and completed replacement side by side with a download link after reload', async () => {
  const ready = { ...job('ready', 'a'.repeat(32), 'video'), output_url: `/api/videos/character-swaps/${job().id}/output` }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [ready] })
  await mount()
  const previews = document.querySelectorAll('video')
  expect(previews).toHaveLength(2)
  expect([...previews].every(element => element.controls)).toBe(true)
  expect(document.querySelector('[data-swap-download]')?.getAttribute('href')).toBe(ready.output_url)
})
it('aborts a late initial load without restarting polling after teardown', async () => {
  let release: (value: { jobs: CharacterSwapJob[] }) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.listCharacterSwaps).mockReturnValue(new Promise(resolve => { release = resolve }))
  await mount()
  const signal = vi.mocked(api.listCharacterSwaps).mock.calls[0]?.[0]
  app?.unmount(); app = undefined
  release({ jobs: [{ ...job('running'), remote_id: 'remote-1' }] }); await settle()
  await vi.advanceTimersByTimeAsync(6000)
  expect(signal?.aborted).toBe(true)
  expect(api.getCharacterSwap).not.toHaveBeenCalled()
  expect(vi.getTimerCount()).toBe(0)
})
it('discards a late paid response when another saved job is selected', async () => {
  const other = job('prepared', 'b'.repeat(32))
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob(), other] })
  let release: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.submitCharacterSwap).mockReturnValue(new Promise(resolve => { release = resolve }))
  await mount(); confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  const signal = vi.mocked(api.submitCharacterSwap).mock.calls[0]?.[2]
  button(`[data-swap-job="${other.id}"]`).click(); await settle()
  release({ ...job('running'), remote_id: 'remote-1' }); await settle()
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector('video')?.getAttribute('src')).toBe(other.clip_url)
  expect(document.querySelector('[data-swap-submit]')).toBeNull()
  await vi.advanceTimersByTimeAsync(6000)
  expect(api.getCharacterSwap).not.toHaveBeenCalled()
})
it('keeps one poll in flight and cancels it on teardown', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job('running'), remote_id: 'remote-1' }] })
  let release: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.getCharacterSwap).mockReturnValue(new Promise(resolve => { release = resolve }))
  await mount(); await vi.advanceTimersByTimeAsync(20000)
  expect(api.getCharacterSwap).toHaveBeenCalledTimes(1)
  const signal = vi.mocked(api.getCharacterSwap).mock.calls[0]?.[1]
  app?.unmount(); app = undefined; release(job('ready')); await settle()
  expect(signal?.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})
it('requires a second quote and new approvals after the edited frame is ready', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob()] })
  const edited = { ...job('prepared', 'a'.repeat(32), 'video'), revision: 2, phase: 'review_frame' }
  vi.mocked(api.getCharacterSwap).mockResolvedValue(edited)
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(quotedJob(edited))
  await mount(); confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  await vi.advanceTimersByTimeAsync(4000); await settle()
  expect(document.querySelector('[data-swap-edited-frame]')?.getAttribute('src')).toBe(edited.edited_frame_url)
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(document.querySelector('[data-swap-submit]')).toBeNull()
  button('[data-swap-quote]').click(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).toHaveBeenCalledTimes(2)
  expect(api.submitCharacterSwap).toHaveBeenLastCalledWith(edited.id, { revision: 2, quote_id: quotedJob(edited).quote?.id, transfers_confirmed: true, consent_confirmed: true }, expect.any(AbortSignal))
})
it('keeps legacy provider jobs read-only while showing their saved output', async () => {
  const legacy = { ...job('ready'), provider: 'fal', model_id: 'fal-ai/wan/v2.2-14b/animate/replace', output_url: `/api/videos/character-swaps/${job().id}/output`, remote_id: 'remote-1' }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [parseCharacterSwapJob(legacy)] })
  await mount()
  expect(document.body.textContent).toContain('Earlier provider')
  expect(document.querySelector('[data-swap-download]')).not.toBeNull()
  expect(document.querySelector('[data-swap-quote]')).toBeNull()
  expect(document.querySelector('[data-swap-resume]')).toBeNull()
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
})
it('links to existing OpenRouter settings when generation is not configured', async () => {
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: false, ffmpeg_ready: true })
  await mount()
  expect(document.querySelector('[data-swap-setup]')?.getAttribute('href')).toBe('/settings#providers')
  expect(document.body.textContent).not.toContain('FAL_KEY')
  expect(document.body.textContent).not.toContain('fal cloud')
})
it('discards an estimate returned after selecting another saved job', async () => {
  const other = job('prepared', 'b'.repeat(32))
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job(), other] })
  let release: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.quoteCharacterSwap).mockReturnValue(new Promise(resolve => { release = resolve }))
  await mount(); button('[data-swap-quote]').click(); await settle()
  const signal = vi.mocked(api.quoteCharacterSwap).mock.calls[0]?.[2]
  button(`[data-swap-job="${other.id}"]`).click(); await settle()
  release(quotedJob()); await settle()
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector('[data-swap-submit]')).toBeNull()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('allows backend-authorized frame recovery without another paid submission', async () => {
  const existing = { ...job('failed'), resume_available: true, frame_receipt_id: 'f'.repeat(32) }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.resumeCharacterSwap).mockResolvedValue(job('prepared', existing.id, 'video'))
  await mount(); button('[data-swap-resume]').click(); await settle()
  expect(api.resumeCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id, { revision: 1 }, expect.any(AbortSignal))
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
})
function selectedCut(seconds = 12): VideoSceneCut { return { id: '1'.repeat(32), source_id: '2'.repeat(32), name: 'Movie scene', start_sec: 120, end_sec: 120 + seconds, seconds, status: 'ready', error_code: '', created_at: 'today', updated_at: 'today', width: 640, height: 480, has_audio: true, output_url: `/api/videos/scenes/sources/${'2'.repeat(32)}/cuts/${'1'.repeat(32)}/media`, output_sha256: '3'.repeat(64), bytes: 1000 } }
it('prepares an owned scene directly with its character image and the budget-compatible two-second default', async () => {
  await mount(selectedCut())
  expect(document.querySelector('[data-swap-video]')).toBeNull()
  expect(document.querySelector<HTMLInputElement>('[data-swap-seconds]')?.value).toBe('2')
  const image = new File(['image'], 'character.png', { type: 'image/png' })
  upload('[data-swap-image]', image); await settle(); button('[data-swap-prepare]').click(); await settle()
  expect(api.prepareCharacterSwapFromScene).toHaveBeenCalledExactlyOnceWith({ sceneId: selectedCut().source_id, cutId: selectedCut().id, image, name: 'Character swap', startSec: 0, seconds: 2 }, expect.any(AbortSignal))
  expect(api.prepareCharacterSwap).not.toHaveBeenCalled()
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
})
it('bounds the whole-second swap interval inside the selected cut', async () => {
  await mount(selectedCut(3.5))
  expect(document.querySelector<HTMLInputElement>('[data-swap-seconds]')?.value).toBe('2')
  upload('[data-swap-image]', new File(['image'], 'character.png', { type: 'image/png' }))
  const start = document.querySelector('[data-swap-start]')
  if (!(start instanceof HTMLInputElement)) throw new Error('Missing start')
  start.value = '2'; start.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(button('[data-swap-prepare]').disabled).toBe(true)
  expect(api.prepareCharacterSwapFromScene).not.toHaveBeenCalled()
})
it('rejects a chosen scene larger than the character-swap source limit', async () => {
  await mount({ ...selectedCut(), bytes: 100 * 1024 * 1024 + 1 })
  upload('[data-swap-image]', new File(['image'], 'character.png', { type: 'image/png' })); await settle()
  expect(button('[data-swap-prepare]').disabled).toBe(true)
  expect(api.prepareCharacterSwapFromScene).not.toHaveBeenCalled()
})
it('aborts scene preparation and discards its response when the chosen scene changes', async () => {
  let release: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.prepareCharacterSwapFromScene).mockReturnValue(new Promise(resolve => { release = resolve }))
  const scene = await mount(selectedCut())
  upload('[data-swap-image]', new File(['image'], 'character.png', { type: 'image/png' })); await settle(); button('[data-swap-prepare]').click(); await settle()
  const signal = vi.mocked(api.prepareCharacterSwapFromScene).mock.calls[0]?.[1]
  scene.value = { ...selectedCut(3), id: '4'.repeat(32), name: 'Other scene', output_url: `/api/videos/scenes/sources/${'2'.repeat(32)}/cuts/${'4'.repeat(32)}/media` }; await settle()
  release(job()); await settle()
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector('[data-swap-chosen-scene]')?.textContent).toContain('Other scene')
  expect(document.querySelector('[data-swap-quote]')).toBeNull()
})
