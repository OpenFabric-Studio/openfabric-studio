// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { CharacterSwapJob, OpenRouterQuote, VideoSceneCut } from '../../api/contracts'
import { parseCharacterSwapJob } from '../../api/contracts'
import * as api from '../../api/characterSwaps'
import { ApiError } from '../../api/http'
import VideoCharacterSwap from './VideoCharacterSwap.vue'

vi.mock('../../api/characterSwaps', () => ({
  CHARACTER_SWAP_MAX_VIDEO_BYTES: 100 * 1024 * 1024, characterSwapReadiness: vi.fn(), listCharacterSwaps: vi.fn(), prepareCharacterSwap: vi.fn(), prepareCharacterSwapFromScene: vi.fn(),
  getCharacterSwap: vi.fn(), quoteCharacterSwap: vi.fn(), reviewCharacterSwap: vi.fn(), submitCharacterSwap: vi.fn(), resumeCharacterSwap: vi.fn(), stopCharacterSwap: vi.fn(),
  renameCharacterSwap: vi.fn(), deleteCharacterSwap: vi.fn(),
}))
let app: App | undefined
const askDelete = vi.fn<(message?: string) => boolean>(() => false)
function job(status: CharacterSwapJob['status'] = 'prepared', id = 'a'.repeat(32), stage: CharacterSwapJob['stage'] = 'frame'): CharacterSwapJob {
  return { id, revision: 1, name: 'New character', status, phase: '', error_code: '',
    created_at: '2026-10-11T10:00:00Z', updated_at: '2026-10-11T10:00:00Z', start_sec: 0, seconds: 2,
    source_name: 'source.mp4', image_name: 'character.png', source_duration_sec: 10, width: 640, height: 480,
    frames: 48, has_audio: true, estimated_usd: 0.24, quote_expires_at: '2026-10-11T12:00:00Z', seed: 42,
    source_url: `/api/videos/character-swaps/${id}/source`, clip_url: `/api/videos/character-swaps/${id}/clip`,
    image_url: `/api/videos/character-swaps/${id}/image`, output_url: '', output_sha256: '', remote_id: null,
    provider: 'openrouter', model_id: stage === 'frame' ? 'bytedance-seed/seedream-4.5' : 'runway/aleph-2', price_checked_at: '2026-10-11',
    stage, quote: null, source_frame_url: `/api/videos/character-swaps/${id}/frame-original`,
    filmstrip_url: '', prompt: stage === 'frame' ? 'Exact backend frame prompt.' : 'Exact backend video prompt.',
    review_fingerprint: (stage === 'frame' ? '7' : '8').repeat(64), preparation_reviewed: true,
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
  for (const field of ['source_url', 'clip_url', 'image_url', 'output_url', 'source_frame_url', 'edited_frame_url', 'filmstrip_url']) {
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
  vi.stubGlobal('confirm', askDelete); askDelete.mockReturnValue(false)
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: true, clip_host_configured: true, ffmpeg_ready: true })
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job()] })
  vi.mocked(api.prepareCharacterSwap).mockResolvedValue(job())
  vi.mocked(api.prepareCharacterSwapFromScene).mockResolvedValue(job())
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(quotedJob())
  vi.mocked(api.submitCharacterSwap).mockResolvedValue({ ...job('queued'), remote_id: 'remote-1' })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('keeps paid submission blocked before saved input review even after cost and rights approval', async () => {
  const existing = quotedJob({ ...job(), preparation_reviewed: false })
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(existing)
  await mount(); confirm(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(button('[data-swap-quote]').disabled).toBe(false)
  button('[data-swap-quote]').click(); await settle()
  expect(api.quoteCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id, { revision: 1 }, expect.any(AbortSignal))
  expect(api.reviewCharacterSwap).not.toHaveBeenCalled()
})
it('saves local input review with its revision and fingerprint and resets paid approvals', async () => {
  const existing = { ...quotedJob(), revision: 4, preparation_reviewed: false }
  const reviewed = { ...existing, revision: 5, preparation_reviewed: true }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.reviewCharacterSwap).mockResolvedValue(reviewed)
  await mount(); confirm(); await settle()
  button('[data-swap-input-review-confirm]').click(); button('[data-swap-input-review-confirm]').click(); await settle()
  expect(api.reviewCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id,
    { revision: 4, review_fingerprint: existing.review_fingerprint }, expect.any(AbortSignal))
  expect(document.querySelector('[data-swap-input-reviewed]')?.textContent).toContain('reviewed for this step')
  expect(document.querySelector('[data-swap-input-review-confirm]')).toBeNull()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect([...document.querySelectorAll<HTMLInputElement>('[data-swap-review] input[type=checkbox]')].every(box => !box.checked)).toBe(true)
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id,
    { revision: 5, quote_id: existing.quote?.id, transfers_confirmed: true, consent_confirmed: true }, expect.any(AbortSignal))
})
it('allows local input review without provider, clip-sharing or FFmpeg readiness', async () => {
  const existing = { ...job(), preparation_reviewed: false }
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: false, clip_host_configured: false, ffmpeg_ready: false })
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.reviewCharacterSwap).mockResolvedValue({ ...existing, revision: 2, preparation_reviewed: true })
  await mount()
  expect(button('[data-swap-input-review-confirm]').disabled).toBe(false)
  button('[data-swap-input-review-confirm]').click(); await settle()
  expect(api.reviewCharacterSwap).toHaveBeenCalledTimes(1)
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('shows the sampled strip with full-playback guidance and the exact read-only backend prompt', async () => {
  const existing = { ...job(), preparation_reviewed: false, filmstrip_url: `/api/videos/character-swaps/${job().id}/filmstrip`, prompt: 'Keep this exact <script>backend text</script>\nwith its line break.' }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  await mount()
  const strip = document.querySelector('[data-swap-filmstrip]')
  expect(strip?.getAttribute('src')).toBe(existing.filmstrip_url)
  expect(strip?.getAttribute('alt')).toContain('Five sampled frames')
  const description = document.getElementById(strip?.getAttribute('aria-describedby') ?? '')
  expect(description?.textContent).toContain('Play the entire selected clip')
  expect(description?.textContent).toContain('does not screen every frame')
  expect(document.querySelector('[data-swap-prompt]')?.textContent).toBe(existing.prompt)
  expect(document.querySelector('[data-swap-prompt] script')).toBeNull()
  expect(document.querySelector('[data-swap-prompt] textarea, [data-swap-prompt] input')).toBeNull()
})
it('keeps earlier saved inputs playable and reviewable when no sampled strip exists', async () => {
  const existing = { ...job(), filmstrip_url: '', preparation_reviewed: false }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  await mount()
  expect(document.querySelector('[data-swap-filmstrip]')).toBeNull()
  expect(document.querySelector('video')?.controls).toBe(true)
  expect(document.querySelector('video')?.getAttribute('src')).toBe(existing.clip_url)
  expect(button('[data-swap-input-review-confirm]').disabled).toBe(false)
  expect(document.querySelector('[data-swap-input-review]')?.textContent).toContain('Play the entire selected clip')
})
it('does not invent a prompt for an earlier record without a backend prompt', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job(), prompt: '' }] })
  await mount()
  expect(document.querySelector('[data-swap-prompt]')).toBeNull()
})
it('retains saved input acceptance after remount while requiring fresh paid approvals', async () => {
  const existing = { ...quotedJob(), preparation_reviewed: false }
  const reviewed = { ...existing, revision: 2, preparation_reviewed: true }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.reviewCharacterSwap).mockResolvedValue(reviewed)
  await mount(); button('[data-swap-input-review-confirm]').click(); await settle()
  app?.unmount(); app = undefined; document.body.replaceChildren()
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [reviewed] })
  await mount()
  expect(document.querySelector('[data-swap-input-reviewed]')).not.toBeNull()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  confirm(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(false)
  expect(api.reviewCharacterSwap).toHaveBeenCalledTimes(1)
})
it.each(['fingerprint', 'renaming'] as const)('blocks local input review without the current %s', async restriction => {
  const existing = { ...job(), preparation_reviewed: false, review_fingerprint: restriction === 'fingerprint' ? '' : job().review_fingerprint }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  await mount()
  if (restriction === 'renaming') { button('[data-swap-rename]').click(); await settle() }
  expect(button('[data-swap-input-review-confirm]').disabled).toBe(true)
  button('[data-swap-input-review-confirm]').click(); await settle()
  expect(api.reviewCharacterSwap).not.toHaveBeenCalled()
})
it.each(['failed', 'running', 'submission_unknown'] as const)('does not offer input review for an ineligible %s job', async status => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job(status), preparation_reviewed: false }] })
  await mount()
  expect(document.querySelector('[data-swap-input-review-confirm]')).toBeNull()
  expect(api.reviewCharacterSwap).not.toHaveBeenCalled()
})
it('allows review of a backend-authorized new attempt before its free estimate', async () => {
  const existing = { ...job('failed', 'a'.repeat(32), 'video'), preparation_reviewed: false, requote_available: true }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.reviewCharacterSwap).mockResolvedValue({ ...existing, revision: 2, preparation_reviewed: true })
  await mount(); button('[data-swap-input-review-confirm]').click(); await settle()
  expect(api.reviewCharacterSwap).toHaveBeenCalledWith(existing.id,
    { revision: 1, review_fingerprint: existing.review_fingerprint }, expect.any(AbortSignal))
  expect(button('[data-swap-requote]').disabled).toBe(false)
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
})
it.each(['selection', 'unmount', 'scene change'] as const)('discards late input acceptance after %s', async teardown => {
  const existing = { ...job(), preparation_reviewed: false }, other = { ...job('prepared', 'b'.repeat(32)), preparation_reviewed: false }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing, other] })
  let finish: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.reviewCharacterSwap).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const scene = await mount(); button('[data-swap-input-review-confirm]').click(); await settle()
  expect(button('[data-swap-input-review-confirm]').disabled).toBe(true)
  const signal = vi.mocked(api.reviewCharacterSwap).mock.calls[0]?.[2]
  if (teardown === 'selection') { button(`[data-swap-job="${other.id}"]`).click(); await settle() }
  else if (teardown === 'scene change') { scene.value = selectedCut(); await settle() }
  else { app?.unmount(); app = undefined }
  finish({ ...existing, revision: 2, preparation_reviewed: true }); await settle(); await vi.advanceTimersByTimeAsync(6000)
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector('[data-swap-input-reviewed]')).toBeNull()
  expect(api.getCharacterSwap).not.toHaveBeenCalled()
  if (teardown === 'selection') expect(document.querySelector('video')?.getAttribute('src')).toBe(other.clip_url)
  if (teardown === 'unmount') expect(vi.getTimerCount()).toBe(0)
})
it('requires fresh local review when the edited frame changes the video-stage inputs', async () => {
  const existing = quotedJob()
  const edited = { ...job('prepared', 'a'.repeat(32), 'video'), revision: 2, preparation_reviewed: false }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.getCharacterSwap).mockResolvedValue(edited)
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(quotedJob(edited))
  await mount(); confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  await vi.advanceTimersByTimeAsync(4000); await settle()
  expect(document.querySelector('[data-swap-input-reviewed]')).toBeNull()
  expect(document.querySelector('[data-swap-prompt]')?.textContent).toBe(edited.prompt)
  expect(button('[data-swap-input-review-confirm]').disabled).toBe(false)
  button('[data-swap-quote]').click(); await settle(); confirm(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).toHaveBeenCalledTimes(1)
})
it('posts and validates the revision-bound local review at the API boundary', async () => {
  const actual = await vi.importActual<typeof import('../../api/characterSwaps')>('../../api/characterSwaps')
  expect(typeof actual.reviewCharacterSwap).toBe('function')
  const response = { ...job(), revision: 2 }
  const fetchReview = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }))
  vi.stubGlobal('fetch', fetchReview)
  const signal = new AbortController().signal, body = { revision: 1, review_fingerprint: '7'.repeat(64) }
  expect(await actual.reviewCharacterSwap(job().id, body, signal)).toEqual(response)
  expect(fetchReview).toHaveBeenCalledExactlyOnceWith(`/api/videos/character-swaps/${job().id}/review`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
  expect(() => actual.reviewCharacterSwap(job().id, { ...body, review_fingerprint: 'bad' }, signal)).toThrow()
  expect(fetchReview).toHaveBeenCalledTimes(1)
})

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
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: true, clip_host_configured: true, ffmpeg_ready: false })
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

function renameInput(value: string) {
  const input = document.querySelector('[data-swap-rename-input]')
  if (!(input instanceof HTMLInputElement)) throw new Error('Missing rename input')
  input.value = value; input.dispatchEvent(new Event('input', { bubbles: true }))
  return input
}
it.each(['prepared', 'ready', 'submission_unknown'] as const)('confirms local deletion of a %s job without cloud calls', async status => {
  const existing = { ...job(status, 'a'.repeat(32), 'video'), revision: 4 }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.deleteCharacterSwap).mockResolvedValue({ deleted: existing.id })
  const ask = askDelete.mockReturnValue(false)
  await mount()
  button('[data-swap-delete]').click(); await settle()
  expect(api.deleteCharacterSwap).not.toHaveBeenCalled()
  expect(document.querySelector(`[data-swap-job="${existing.id}"]`)).not.toBeNull()
  expect(ask).toHaveBeenCalledWith(expect.stringContaining(existing.name))
  expect(ask.mock.calls[0]?.[0]).toContain('cannot be undone')
  expect(ask.mock.calls[0]?.[0]).toContain('Billing receipts remain')
  expect(ask.mock.calls[0]?.[0]).toContain('does not cancel')
  if (status === 'submission_unknown') {
    expect(document.body.textContent).toContain('paid submission outcome is unknown')
    expect(ask.mock.calls[0]?.[0]).toContain('may have been billed')
  }
  ask.mockReturnValue(true)
  button('[data-swap-delete]').click(); await settle()
  expect(api.deleteCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id, { revision: 4 }, expect.any(AbortSignal))
  expect(document.querySelector(`[data-swap-job="${existing.id}"]`)).toBeNull()
  expect(document.querySelector('video')).toBeNull()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(api.stopCharacterSwap).not.toHaveBeenCalled()
})
it('selects the retained neighbor and restores owned focus after deletion', async () => {
  const other = { ...job('ready', 'b'.repeat(32)), name: 'Other replacement' }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job(), other] })
  vi.mocked(api.deleteCharacterSwap).mockResolvedValue({ deleted: job().id })
  askDelete.mockReturnValue(true)
  await mount()
  const remove = button('[data-swap-delete]'); remove.focus(); remove.click(); await settle()
  expect(document.activeElement).toBe(button(`[data-swap-job="${other.id}"]`))
  expect(document.querySelector('video')?.getAttribute('src')).toBe(other.clip_url)
})
it('focuses the saved-job heading when deleting the final job', async () => {
  vi.mocked(api.deleteCharacterSwap).mockResolvedValue({ deleted: job().id })
  askDelete.mockReturnValue(true)
  await mount()
  const remove = button('[data-swap-delete]'); remove.focus(); remove.click(); await settle()
  expect(document.activeElement?.id).toBe('character-swap-library-heading')
  expect(document.body.textContent).toContain('No saved character swaps yet')
})
it('keeps saved media on deletion failure and displays only a safe error', async () => {
  vi.mocked(api.deleteCharacterSwap).mockRejectedValue(new ApiError('storage_unavailable', 503))
  askDelete.mockReturnValue(true)
  await mount(); button('[data-swap-delete]').click(); await settle()
  expect(document.querySelector(`[data-swap-job="${job().id}"]`)).not.toBeNull()
  expect(document.querySelector('video')?.getAttribute('src')).toBe(job().clip_url)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('local library')
  expect(button('[data-swap-delete]').disabled).toBe(false)
})
it.each(['submitting', 'queued', 'running'] as const)('requires stopping a %s job before local management', async status => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job(status)] })
  await mount()
  expect(button('[data-swap-rename]').disabled).toBe(true)
  expect(button('[data-swap-delete]').disabled).toBe(true)
  expect(button('[data-swap-stop]').disabled).toBe(false)
  expect(document.body.textContent).toContain('Stop tracking and wait for local cleanup')
})
it('offers Stop tracking for unresolved cleanup and enables management only after it settles', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job('stopped'), error_code: 'worker_unresolved' }] })
  vi.mocked(api.stopCharacterSwap).mockResolvedValue(job('stopped'))
  await mount()
  expect(button('[data-swap-delete]').disabled).toBe(true)
  button('[data-swap-stop]').click(); await settle()
  expect(api.stopCharacterSwap).toHaveBeenCalledExactlyOnceWith(job().id, { revision: 1 }, expect.any(AbortSignal))
  expect(button('[data-swap-delete]').disabled).toBe(false)
})
it('renames a saved result with a revision-bound request and clears its paid estimate', async () => {
  const existing = { ...quotedJob(job('prepared', 'a'.repeat(32), 'video')), revision: 4 }
  const renamed = { ...existing, name: 'Reviewed scene', revision: 5, quote: null }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.renameCharacterSwap).mockResolvedValue(renamed)
  await mount(); confirm(); await settle()
  button('[data-swap-rename]').click(); await settle()
  expect(document.activeElement).toBe(renameInput('  Reviewed scene  ')); await settle()
  button('[data-swap-rename-save]').click(); await settle()
  expect(api.renameCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id, { revision: 4, name: 'Reviewed scene' }, expect.any(AbortSignal))
  expect(button(`[data-swap-job="${existing.id}"]`).textContent).toContain('Reviewed scene')
  expect(document.querySelector('[data-swap-edited-frame]')?.getAttribute('src')).toBe(existing.edited_frame_url)
  expect(document.querySelector('[data-swap-submit]')).toBeNull()
  expect(document.querySelector('[data-swap-rename-input]')).toBeNull()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('validates a rename draft and supports cancelling it', async () => {
  await mount(); button('[data-swap-rename]').click(); await settle()
  renameInput('   '); await settle()
  expect(button('[data-swap-rename-save]').disabled).toBe(true)
  renameInput('x'.repeat(121)); await settle()
  expect(button('[data-swap-rename-save]').disabled).toBe(true)
  button('[data-swap-rename-cancel]').click(); await settle()
  expect(document.querySelector('[data-swap-rename-input]')).toBeNull()
  expect(button(`[data-swap-job="${job().id}"]`).textContent).toContain(job().name)
  expect(api.renameCharacterSwap).not.toHaveBeenCalled()
})
it('allows inactive legacy records to be renamed or deleted without provider actions', async () => {
  const existing = parseCharacterSwapJob({ ...job('ready'), provider: 'fal', model_id: 'fal-ai/wan/v2.2-14b/animate/replace', output_url: `/api/videos/character-swaps/${job().id}/output` })
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.renameCharacterSwap).mockResolvedValue({ ...existing, revision: 2, name: 'Earlier result' })
  vi.mocked(api.deleteCharacterSwap).mockResolvedValue({ deleted: existing.id })
  askDelete.mockReturnValue(true)
  await mount(); button('[data-swap-rename]').click(); await settle()
  renameInput('Earlier result'); await settle(); button('[data-swap-rename-save]').click(); await settle()
  expect(api.renameCharacterSwap).toHaveBeenCalledTimes(1)
  button('[data-swap-delete]').click(); await settle()
  expect(api.deleteCharacterSwap).toHaveBeenCalledWith(existing.id, { revision: 2 }, expect.any(AbortSignal))
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(api.resumeCharacterSwap).not.toHaveBeenCalled()
})
it.each(['rename', 'delete'] as const)('blocks selection during pending %s and aborts it on teardown', async action => {
  const other = job('prepared', 'b'.repeat(32))
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job(), other] })
  let finish: () => void = () => { throw new Error('Uninitialized') }
  if (action === 'rename') vi.mocked(api.renameCharacterSwap).mockReturnValue(new Promise(resolve => { finish = () => resolve({ ...job(), name: 'Renamed', revision: 2 }) }))
  else vi.mocked(api.deleteCharacterSwap).mockReturnValue(new Promise(resolve => { finish = () => resolve({ deleted: job().id }) }))
  askDelete.mockReturnValue(true)
  await mount()
  if (action === 'rename') { button('[data-swap-rename]').click(); await settle(); renameInput('Renamed'); await settle(); button('[data-swap-rename-save]').click() }
  else button('[data-swap-delete]').click()
  await settle()
  const signal = action === 'rename' ? vi.mocked(api.renameCharacterSwap).mock.calls[0]?.[2] : vi.mocked(api.deleteCharacterSwap).mock.calls[0]?.[2]
  expect(button(`[data-swap-job="${other.id}"]`).disabled).toBe(true)
  button(`[data-swap-job="${other.id}"]`).click(); await settle()
  expect(document.querySelector('video')?.getAttribute('src')).toBe(job().clip_url)
  app?.unmount(); app = undefined; finish(); await settle()
  expect(signal?.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})
it('ignores an old poll after stopping and deleting its saved job', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [job('running')] })
  let finish: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.getCharacterSwap).mockReturnValue(new Promise(resolve => { finish = resolve }))
  vi.mocked(api.stopCharacterSwap).mockResolvedValue(job('stopped'))
  vi.mocked(api.deleteCharacterSwap).mockResolvedValue({ deleted: job().id })
  askDelete.mockReturnValue(true)
  await mount(); await vi.advanceTimersByTimeAsync(4000)
  const signal = vi.mocked(api.getCharacterSwap).mock.calls[0]?.[1]
  button('[data-swap-stop]').click(); await settle()
  button('[data-swap-delete]').click(); await settle()
  finish(job('ready')); await settle(); await vi.advanceTimersByTimeAsync(6000)
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector(`[data-swap-job="${job().id}"]`)).toBeNull()
  expect(api.getCharacterSwap).toHaveBeenCalledTimes(1)
})
it('pauses both saved previews before removing their local media', async () => {
  const existing = { ...job('ready', 'a'.repeat(32), 'video'), output_url: `/api/videos/character-swaps/${job().id}/output` }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.deleteCharacterSwap).mockResolvedValue({ deleted: existing.id })
  askDelete.mockReturnValue(true)
  await mount()
  const players = [...document.querySelectorAll('video')]
  expect(players).toHaveLength(2)
  for (const player of players) player.dispatchEvent(new Event('play'))
  const paused = players.map(player => vi.spyOn(player, 'pause'))
  button('[data-swap-delete]').click(); await settle()
  expect(paused.every(pause => pause.mock.calls.length > 0)).toBe(true)
  expect(document.querySelector('video')).toBeNull()
})
it('keeps external focus when a pending deletion completes', async () => {
  let finish: () => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.deleteCharacterSwap).mockReturnValue(new Promise(resolve => { finish = () => resolve({ deleted: job().id }) }))
  askDelete.mockReturnValue(true)
  await mount()
  const remove = button('[data-swap-delete]'); remove.focus(); remove.click(); await settle()
  const elsewhere = document.body.appendChild(document.createElement('button')); elsewhere.textContent = 'Elsewhere'; elsewhere.focus()
  finish(); await settle()
  expect(document.activeElement).toBe(elsewhere)
})
it('retains the selected job when deletion returns a different job identity', async () => {
  vi.mocked(api.deleteCharacterSwap).mockResolvedValue({ deleted: 'b'.repeat(32) })
  askDelete.mockReturnValue(true)
  await mount(); button('[data-swap-delete]').click(); await settle()
  expect(document.querySelector(`[data-swap-job="${job().id}"]`)).not.toBeNull()
  expect(document.querySelector('[role=alert]')?.textContent).toContain('Refresh saved jobs')
})
it('retains a failed rename draft without exposing an internal error', async () => {
  vi.mocked(api.renameCharacterSwap).mockRejectedValue(new Error('/private/library/path'))
  await mount(); button('[data-swap-rename]').click(); await settle()
  renameInput('My saved draft'); await settle(); button('[data-swap-rename-save]').click(); await settle()
  expect(document.querySelector<HTMLInputElement>('[data-swap-rename-input]')?.value).toBe('My saved draft')
  expect(button(`[data-swap-job="${job().id}"]`).textContent).toContain(job().name)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('Could not complete this action')
  expect(document.body.textContent).not.toContain('/private/library/path')
})
function rejectedVideo(): CharacterSwapJob {
  return { ...job('failed', 'a'.repeat(32), 'video'), revision: 2, error_code: 'provider_request_rejected', video_receipt_id: 'e'.repeat(32), requote_available: true }
}
it('reviews a free new video estimate using the saved edited frame before a separate paid attempt', async () => {
  const existing = rejectedVideo()
  const fresh = { ...quotedJob({ ...existing, status: 'prepared', revision: 3 }), requote_available: false }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(fresh)
  vi.mocked(api.submitCharacterSwap).mockResolvedValue({ ...fresh, status: 'queued', remote_id: 'remote-new' })
  await mount()
  expect(document.querySelector('[data-swap-edited-frame]')?.getAttribute('src')).toBe(existing.edited_frame_url)
  expect(document.body.textContent).toContain('saved edited frame')
  expect(document.body.textContent).toContain('separate paid attempt')
  button('[data-swap-requote]').click(); await settle()
  expect(api.quoteCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id, { revision: 2 }, expect.any(AbortSignal))
  expect(api.prepareCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect([...document.querySelectorAll<HTMLInputElement>('[data-swap-review] input[type=checkbox]')].every(box => !box.checked)).toBe(true)
  confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  expect(api.submitCharacterSwap).toHaveBeenCalledExactlyOnceWith(existing.id, { revision: 3, quote_id: fresh.quote?.id, transfers_confirmed: true, consent_confirmed: true }, expect.any(AbortSignal))
})
it.each(['failed', 'submission_unknown', 'stopped'] as const)('does not offer a new estimate for an ineligible %s job', async status => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job(status, 'a'.repeat(32), 'video'), error_code: 'provider_request_rejected', requote_available: false }] })
  await mount()
  expect(document.querySelector('[data-swap-requote]')).toBeNull()
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('clears earlier paid approvals when a rejected video receives a new quote', async () => {
  const existing = quotedJob(job('prepared', 'a'.repeat(32), 'video'))
  const rejected = rejectedVideo()
  const fresh = { ...quotedJob({ ...rejected, status: 'prepared', revision: 3 }), requote_available: false }
  if (!fresh.quote) throw new Error('Missing fixture quote')
  fresh.quote.id = '9'.repeat(32)
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.submitCharacterSwap).mockResolvedValue(rejected)
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(fresh)
  await mount(); confirm(); await settle(); button('[data-swap-submit]').click(); await settle()
  expect(document.querySelector('[data-swap-submit]')).toBeNull()
  button('[data-swap-requote]').click(); await settle()
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect([...document.querySelectorAll<HTMLInputElement>('[data-swap-review] input[type=checkbox]')].every(box => !box.checked)).toBe(true)
  expect(api.submitCharacterSwap).toHaveBeenCalledTimes(1)
})
it.each(['configuration', 'ffmpeg', 'renaming'] as const)('blocks a free new estimate while %s is unavailable', async restriction => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [rejectedVideo()] })
  if (restriction === 'configuration') vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: false, ffmpeg_ready: true })
  if (restriction === 'ffmpeg') vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: true, clip_host_configured: true, ffmpeg_ready: false })
  await mount()
  if (restriction === 'renaming') { button('[data-swap-rename]').click(); await settle() }
  expect(button('[data-swap-requote]').disabled).toBe(true)
  button('[data-swap-requote]').click(); await settle()
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
})
it.each(['selection', 'unmount'] as const)('discards a late new video estimate after %s', async teardown => {
  const existing = rejectedVideo(), other = job('prepared', 'b'.repeat(32))
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing, other] })
  let finish: (value: CharacterSwapJob) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.quoteCharacterSwap).mockReturnValue(new Promise(resolve => { finish = resolve }))
  await mount(); button('[data-swap-requote]').click(); await settle()
  expect(button('[data-swap-requote]').disabled).toBe(true)
  const signal = vi.mocked(api.quoteCharacterSwap).mock.calls[0]?.[2]
  if (teardown === 'selection') { button(`[data-swap-job="${other.id}"]`).click(); await settle() }
  else { app?.unmount(); app = undefined }
  finish({ ...quotedJob({ ...existing, status: 'prepared' }), requote_available: false }); await settle(); await vi.advanceTimersByTimeAsync(6000)
  expect(signal?.aborted).toBe(true)
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(api.getCharacterSwap).not.toHaveBeenCalled()
  if (teardown === 'selection') {
    expect(document.querySelector('video')?.getAttribute('src')).toBe(other.clip_url)
    expect(document.querySelector('[data-swap-submit]')).toBeNull()
  } else expect(vi.getTimerCount()).toBe(0)
})
it('shows missing hosting separately and blocks frame estimates before opening a relay or making a charge', async () => {
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: false, enabled: true, credential_configured: true, clip_host_configured: false, ffmpeg_ready: true })
  await mount()
  expect(document.body.textContent).toContain('temporary clip sharing')
  expect(document.body.textContent).not.toContain('Enable OpenRouter and configure its key')
  expect(button('[data-swap-quote]').disabled).toBe(true)
  button('[data-swap-quote]').click(); await settle()
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('reviews the temporary public clip relay and requires the Mac to remain online', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob(job('prepared', 'a'.repeat(32), 'video'))] })
  await mount()
  const review = document.querySelector('[data-swap-review]')
  expect(review?.textContent).toContain('Cloudflare')
  expect(review?.textContent).toContain('Anyone with the URL')
  expect(review?.textContent).toContain('24 hours')
  expect(review?.textContent).toContain('Mac and app running')
  expect(review?.textContent).toContain('restart')
})
it('labels clip sharing progress without implying a new accepted AI video job', async () => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job('running', 'a'.repeat(32), 'video'), phase: 'sharing_clip' }] })
  await mount()
  expect(document.body.textContent).toContain('Sharing the selected silent clip')
})
it('requires clip-sharing readiness independently of the OpenRouter configuration flag', async () => {
  vi.mocked(api.characterSwapReadiness).mockResolvedValue({ configured: true, clip_host_configured: false, ffmpeg_ready: true })
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [quotedJob()] })
  await mount()
  expect(button('[data-swap-quote]').disabled).toBe(true)
  expect(button('[data-swap-submit]').disabled).toBe(true)
  expect([...document.querySelectorAll<HTMLInputElement>('[data-swap-review] input[type=checkbox]')].every(box => box.disabled && !box.checked)).toBe(true)
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
it('labels a backend-authorized pre-charge frame recovery without claiming an edited frame exists', async () => {
  const existing = { ...job('failed'), error_code: 'clip_host_unavailable', frame_receipt_id: 'f'.repeat(32), requote_available: true }
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [existing] })
  vi.mocked(api.quoteCharacterSwap).mockResolvedValue(quotedJob({ ...existing, status: 'prepared', revision: 2, requote_available: false }))
  await mount()
  expect(button('[data-swap-requote]').textContent).toContain('Review new frame estimate (free)')
  expect(document.body.textContent).not.toContain('reuses your saved edited frame')
  button('[data-swap-requote]').click(); await settle()
  expect(api.quoteCharacterSwap).toHaveBeenCalledWith(existing.id, { revision: 1 }, expect.any(AbortSignal))
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
  expect(button('[data-swap-submit]').disabled).toBe(true)
})
it.each([
  ['clip_host_binary_missing', 'Install cloudflared'],
  ['clip_host_unavailable', 'HTTPS connection could not fetch'],
  ['clip_host_tunnel_failed', 'could not start'],
  ['clip_host_edge_not_ready', 'not ready'],
  ['clip_host_delivery_invalid', 'No paid submission was sent'],
  ['clip_host_storage_unavailable', 'local clip-sharing metadata'],
  ['clip_host_worker_unresolved', 'ownership could not be verified'],
  ['clip_host_stopping', 'shutting down'],
])('explains the saved clip-sharing failure %s without submitting again', async (code, message) => {
  vi.mocked(api.listCharacterSwaps).mockResolvedValue({ jobs: [{ ...job('failed', 'a'.repeat(32), 'video'), error_code: code }] })
  await mount()
  expect(document.querySelector('[role=alert]')?.textContent).toContain(message)
  if (code !== 'clip_host_binary_missing') expect(document.querySelector('[role=alert]')?.textContent).not.toContain('Install cloudflared')
  expect(api.quoteCharacterSwap).not.toHaveBeenCalled()
  expect(api.submitCharacterSwap).not.toHaveBeenCalled()
})
