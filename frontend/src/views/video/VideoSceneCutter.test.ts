// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import type { VideoSceneSource, VideoSceneCut, VideoScenePreview } from '../../api/contracts'
import { parseVideoScenePreview, parseVideoSceneCut } from '../../api/contracts'
import { ApiError } from '../../api/http'
import * as api from '../../api/videoScenes'
import VideoSceneCutter from './VideoSceneCutter.vue'
vi.mock('../../api/videoScenes', () => ({ sceneReadiness: vi.fn(), listSceneSources: vi.fn(), pickSceneSource: vi.fn(), uploadSceneSource: vi.fn(), getSceneSource: vi.fn(), previewSceneSource: vi.fn(), listSceneCuts: vi.fn(), createSceneCut: vi.fn(), getSceneCut: vi.fn(), cancelSceneCut: vi.fn(), deleteSceneCut: vi.fn() }))
let app: App | undefined
const askDelete = vi.fn<(message?: string) => boolean>(() => false)
const source: VideoSceneSource = { id: 'a'.repeat(32), name: 'Movie.avi', origin: 'native', duration_sec: 3600, width: 640, height: 480, fps: 24, has_audio: true, bytes: 1000, created_at: '2026-10-11T10:00:00Z' }
function preview(id = source.id, start = 0): VideoScenePreview { return { id: 'b'.repeat(32), source_id: id, start_sec: start, duration_sec: 15, width: 640, height: 480, url: `/api/videos/scenes/sources/${id}/previews/${'b'.repeat(32)}` } }
function cut(status: VideoSceneCut['status'] = 'ready'): VideoSceneCut { return { id: 'c'.repeat(32), source_id: source.id, name: 'Scene', start_sec: 120, end_sec: 124, seconds: 4, status, error_code: '', created_at: '2026-10-11T10:00:00Z', updated_at: '2026-10-11T10:00:00Z', width: 640, height: 480, has_audio: true, output_url: status === 'ready' ? `/api/videos/scenes/sources/${source.id}/cuts/${'c'.repeat(32)}/media` : '', output_sha256: '', bytes: 500 } }
async function settle() { for (let index = 0; index < 12; index++) await nextTick() }
function button(selector: string): HTMLButtonElement { const value = document.querySelector(selector); if (!(value instanceof HTMLButtonElement)) throw new Error(`Missing ${selector}`); return value }
function input(selector: string, value: number) { const node = document.querySelector(selector); if (!(node instanceof HTMLInputElement)) throw new Error(`Missing ${selector}`); node.value = String(value); node.dispatchEvent(new Event('input', { bubbles: true })) }
async function mount(cutDeleted = vi.fn<(cut: VideoSceneCut) => void>()) { const useCut = vi.fn(); app = createApp({ render: () => h(VideoSceneCutter, { onUseCut: useCut, onCutDeleted: cutDeleted }) }).use(i18n); app.mount(document.body.appendChild(document.createElement('div'))); await settle(); return useCut }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks()
  vi.stubGlobal('confirm', askDelete); askDelete.mockReturnValue(false)
  vi.mocked(api.sceneReadiness).mockResolvedValue({ ffmpeg_ready: true, native_picker_available: true, max_upload_bytes: 2147483648, max_native_bytes: 8589934592, max_cut_seconds: 600, preview_seconds: 15 })
  vi.mocked(api.listSceneSources).mockResolvedValue({ sources: [source] })
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [cut()] })
  vi.mocked(api.previewSceneSource).mockResolvedValue(preview())
  vi.mocked(api.pickSceneSource).mockResolvedValue(source)
  vi.mocked(api.uploadSceneSource).mockResolvedValue(source)
  vi.mocked(api.createSceneCut).mockResolvedValue(cut('queued'))
  vi.mocked(api.cancelSceneCut).mockResolvedValue(cut('cancelled'))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals(); vi.useRealTimers() })
it('loads a short local preview and saved cuts without loading the original AVI in the browser', async () => {
  await mount()
  expect(document.querySelector('[data-scene-preview]')?.getAttribute('src')).toBe(preview().url)
  expect(document.querySelector('[data-scene-download]')?.getAttribute('href')).toBe(cut().output_url)
  expect(api.previewSceneSource).toHaveBeenCalledExactlyOnceWith(source.id, { start_sec: 0 }, expect.any(AbortSignal))
})
it('uses the advertised native picker without uploading a file', async () => {
  await mount(); button('[data-scene-pick]').click(); await settle()
  expect(api.pickSceneSource).toHaveBeenCalledTimes(1)
  expect(api.uploadSceneSource).not.toHaveBeenCalled()
})
it('disables native selection when the backend does not support it', async () => {
  vi.mocked(api.sceneReadiness).mockResolvedValue({ ffmpeg_ready: true, native_picker_available: false })
  await mount(); expect(button('[data-scene-pick]').disabled).toBe(true)
  button('[data-scene-pick]').click(); expect(api.pickSceneSource).not.toHaveBeenCalled()
})
it('seeks across the whole movie and marks the source time from preview playback', async () => {
  vi.mocked(api.previewSceneSource).mockResolvedValueOnce(preview()).mockResolvedValueOnce(preview(source.id, 120))
  await mount(); input('[data-scene-position]', 120); await settle(); button('[data-scene-seek]').click(); await settle()
  expect(api.previewSceneSource).toHaveBeenLastCalledWith(source.id, { start_sec: 120 }, expect.any(AbortSignal))
  const player = document.querySelector('[data-scene-preview]')
  if (!(player instanceof HTMLVideoElement)) throw new Error('Missing preview')
  player.currentTime = 2; player.dispatchEvent(new Event('timeupdate')); button('[data-scene-mark-in]').click(); await settle()
  player.currentTime = 6; player.dispatchEvent(new Event('timeupdate')); button('[data-scene-mark-out]').click(); await settle()
  expect(document.querySelector<HTMLInputElement>('[data-scene-in]')?.value).toBe('122')
  expect(document.querySelector<HTMLInputElement>('[data-scene-out]')?.value).toBe('126')
  expect(button('[data-scene-create]').disabled).toBe(false)
  expect([...document.querySelectorAll<HTMLInputElement>('form input')].map(value => ({ value: value.value, message: value.validationMessage }))).toEqual([{ value: 'Scene', message: '' }, { value: '122', message: '' }, { value: '126', message: '' }])
  button('[data-scene-create]').click(); await settle()
  expect(api.createSceneCut).toHaveBeenCalledExactlyOnceWith(source.id, { name: 'Scene', start_sec: 122, end_sec: 126 }, expect.any(AbortSignal))
})
it('rejects inverted, overlong and out-of-source ranges before creating a scene', async () => {
  await mount()
  for (const [start, end] of [[5, 5], [6, 5], [0, 601], [3599, 3601]]) {
    input('[data-scene-in]', start); input('[data-scene-out]', end); await settle()
    expect(button('[data-scene-create]').disabled).toBe(true)
  }
  expect(api.createSceneCut).not.toHaveBeenCalled()
})
it('labels in/out fields separately from their playback mark buttons', async () => {
  await mount()
  expect(document.querySelector('[data-scene-in]')?.closest('label')?.textContent).toBe('In point (seconds)')
  expect(document.querySelector('[data-scene-out]')?.closest('label')?.textContent).toBe('Out point (seconds)')
})
it('hands off only a ready scene of at least two seconds', async () => {
  const emit = await mount(); button('[data-scene-use]').click(); await settle()
  expect(emit).toHaveBeenCalledExactlyOnceWith(cut())
})
it('keeps short exports downloadable without offering character swap', async () => {
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [{ ...cut(), seconds: 1, end_sec: 121 }] })
  await mount(); expect(document.querySelector('[data-scene-download]')).not.toBeNull(); expect(document.querySelector('[data-scene-use]')).toBeNull()
})
it('polls durable cut jobs and cancels the selected job without deleting its source', async () => {
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [cut('cutting')] })
  await mount(); button('[data-scene-cancel]').click(); await settle()
  expect(api.cancelSceneCut).toHaveBeenCalledExactlyOnceWith(source.id, cut().id, expect.any(AbortSignal))
  expect(document.body.textContent).toContain('Cancelled')
  expect(api.previewSceneSource).not.toHaveBeenCalled()
})
it('rejects external preview and output media URLs before the browser loads them', () => {
  expect(() => parseVideoScenePreview({ ...preview(), url: 'https://example.com/movie.mp4' })).toThrow()
  expect(() => parseVideoSceneCut({ ...cut(), output_url: 'https://example.com/movie.mp4' })).toThrow()
})
it('keeps completed scenes available when the original movie has moved', async () => {
  vi.mocked(api.previewSceneSource).mockRejectedValue(new ApiError('source_changed', 409))
  await mount()
  expect(document.querySelector('[data-scene-download]')?.getAttribute('href')).toBe(cut().output_url)
  expect(document.body.textContent).toContain('original movie moved')
})
it('rejects an upload over two GiB before sending it to the backend', async () => {
  await mount()
  const file = new File(['movie'], 'Movie.avi', { type: 'video/x-msvideo' })
  Object.defineProperty(file, 'size', { value: 2147483649 })
  const transfer = new DataTransfer(); transfer.items.add(file)
  const control = document.querySelector('[data-scene-upload]')
  if (!(control instanceof HTMLInputElement)) throw new Error('Missing upload')
  control.files = transfer.files; control.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(api.uploadSceneSource).not.toHaveBeenCalled()
  expect(document.body.textContent).toContain('movie exceeds the size limit')
})
it('accepts an MKV movie upload and advertises it in the file chooser', async () => {
  await mount()
  const file = new File(['movie'], 'Movie.MKV', { type: 'video/x-matroska' })
  const transfer = new DataTransfer(); transfer.items.add(file)
  const control = document.querySelector('[data-scene-upload]')
  if (!(control instanceof HTMLInputElement)) throw new Error('Missing upload')
  control.files = transfer.files; control.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(api.uploadSceneSource).toHaveBeenCalledExactlyOnceWith(file, expect.any(AbortSignal))
  expect(control.accept.split(',')).toContain('.mkv')
})
it('keeps an oversized scene downloadable while requiring a shorter export for character swapping', async () => {
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [{ ...cut(), bytes: 100 * 1024 * 1024 + 1 }] })
  const emit = await mount()
  expect(document.querySelector('[data-scene-download]')).not.toBeNull()
  expect(button('[data-scene-use]').disabled).toBe(true)
  expect(document.body.textContent).toContain('Export a shorter scene first')
  button('[data-scene-use]').click(); expect(emit).not.toHaveBeenCalled()
})
it('discards a late preview after teardown without restarting polling', async () => {
  let release: (value: VideoScenePreview) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.previewSceneSource).mockReturnValue(new Promise(resolve => { release = resolve }))
  await mount(); const signal = vi.mocked(api.previewSceneSource).mock.calls[0]?.[2]
  app?.unmount(); app = undefined; release(preview()); await settle(); await vi.advanceTimersByTimeAsync(10000)
  expect(signal?.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0)
})
it('keeps one cut polling request in flight and aborts it on teardown', async () => {
  vi.mocked(api.listSceneCuts).mockResolvedValueOnce({ cuts: [cut('cutting')] })
  let release: (value: { cuts: VideoSceneCut[] }) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.listSceneCuts).mockReturnValueOnce(new Promise(resolve => { release = resolve }))
  await mount(); await vi.advanceTimersByTimeAsync(15000)
  expect(api.listSceneCuts).toHaveBeenCalledTimes(2)
  const signal = vi.mocked(api.listSceneCuts).mock.calls[1]?.[1]
  app?.unmount(); app = undefined; release({ cuts: [cut()] }); await settle()
  expect(signal?.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0)
})
it('discards a stale preview after selecting a different movie', async () => {
  const other = { ...source, id: 'd'.repeat(32), name: 'Other movie.avi' }
  vi.mocked(api.listSceneSources).mockResolvedValue({ sources: [source, other] })
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [] })
  let release: (value: VideoScenePreview) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.previewSceneSource).mockReturnValueOnce(new Promise(resolve => { release = resolve })).mockResolvedValueOnce(preview(other.id, 0))
  await mount()
  const signal = vi.mocked(api.previewSceneSource).mock.calls[0]?.[2]
  const select = document.querySelector('[data-scene-source]')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing source selector')
  select.value = other.id; select.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  release(preview()); await settle()
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector('[data-scene-preview]')?.getAttribute('src')).toBe(preview(other.id).url)
})
it('confirms the named time range and deletes only the saved scene', async () => {
  const deleted = vi.fn<(cut: VideoSceneCut) => void>()
  vi.mocked(api.deleteSceneCut).mockResolvedValue({ source_id: source.id, deleted: cut().id })
  await mount(deleted)
  button('[data-scene-delete]').click(); await settle()
  expect(api.deleteSceneCut).not.toHaveBeenCalled()
  expect(askDelete).toHaveBeenCalledWith(expect.stringContaining('Scene'))
  expect(askDelete.mock.calls[0]?.[0]).toContain('2:00–2:04')
  expect(askDelete.mock.calls[0]?.[0]).toContain('original movie')
  expect(askDelete.mock.calls[0]?.[0]).toContain('prepared character swaps')
  askDelete.mockReturnValue(true)
  button('[data-scene-delete]').click(); await settle()
  expect(api.deleteSceneCut).toHaveBeenCalledExactlyOnceWith(source.id, cut().id, expect.any(AbortSignal))
  expect(deleted).toHaveBeenCalledExactlyOnceWith(cut())
  expect(document.querySelector('[data-scene-download]')).toBeNull()
  expect(document.querySelector('[data-scene-preview]')?.getAttribute('src')).toBe(preview().url)
  expect(document.querySelector<HTMLSelectElement>('[data-scene-source]')?.value).toBe(source.id)
})
it.each(['queued', 'cutting'] as const)('requires cancellation before deleting a %s scene', async status => {
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [cut(status)] })
  vi.mocked(api.deleteSceneCut).mockResolvedValue({ source_id: source.id, deleted: cut().id })
  askDelete.mockReturnValue(true)
  await mount()
  expect(button('[data-scene-delete]').disabled).toBe(true)
  expect(document.body.textContent).toContain('Cancel the cut and wait for local cleanup')
  button('[data-scene-cancel]').click(); await settle()
  expect(button('[data-scene-delete]').disabled).toBe(false)
  button('[data-scene-delete]').click(); await settle()
  expect(api.cancelSceneCut).toHaveBeenCalledTimes(1)
  expect(api.deleteSceneCut).toHaveBeenCalledTimes(1)
})
it('retains scenes whose worker cleanup remains unresolved', async () => {
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [{ ...cut('failed'), error_code: 'worker_unresolved' }] })
  await mount()
  expect(button('[data-scene-delete]').disabled).toBe(true)
  button('[data-scene-delete]').click()
  expect(api.deleteSceneCut).not.toHaveBeenCalled()
  expect(askDelete).not.toHaveBeenCalled()
})
it('restores owned focus to an adjacent scene action after deletion', async () => {
  const other = { ...cut(), id: 'd'.repeat(32), name: 'Another scene', output_url: `/api/videos/scenes/sources/${source.id}/cuts/${'d'.repeat(32)}/media` }
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [cut(), other] })
  vi.mocked(api.deleteSceneCut).mockResolvedValue({ source_id: source.id, deleted: cut().id })
  askDelete.mockReturnValue(true)
  await mount()
  const remove = button(`[data-scene-delete="${cut().id}"]`); remove.focus(); remove.click(); await settle()
  expect(document.activeElement).toBe(button(`[data-scene-delete="${other.id}"]`))
  expect(document.querySelector('[data-scene-download]')?.getAttribute('href')).toBe(other.output_url)
})
it('focuses the saved-scenes heading after removing the final scene', async () => {
  vi.mocked(api.deleteSceneCut).mockResolvedValue({ source_id: source.id, deleted: cut().id })
  askDelete.mockReturnValue(true)
  await mount()
  const remove = button('[data-scene-delete]'); remove.focus(); remove.click(); await settle()
  expect(document.activeElement?.id).toBe('scene-cutter-saved-heading')
  expect(document.body.textContent).toContain('No saved scenes for this movie yet')
})
it('blocks source changes and handoff while deleting and aborts a late response on teardown', async () => {
  const other = { ...source, id: 'd'.repeat(32), name: 'Another movie' }
  vi.mocked(api.listSceneSources).mockResolvedValue({ sources: [source, other] })
  let finish: () => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.deleteSceneCut).mockReturnValue(new Promise(resolve => { finish = () => resolve({ source_id: source.id, deleted: cut().id }) }))
  askDelete.mockReturnValue(true)
  const deleted = vi.fn<(cut: VideoSceneCut) => void>(), use = await mount(deleted)
  button('[data-scene-delete]').click(); await settle()
  expect(button('[data-scene-use]').disabled).toBe(true)
  button('[data-scene-use]').click(); expect(use).not.toHaveBeenCalled()
  const select = document.querySelector('[data-scene-source]')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing source selector')
  expect(select.disabled).toBe(true)
  select.value = other.id; select.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(api.listSceneCuts).toHaveBeenCalledTimes(1)
  const signal = vi.mocked(api.deleteSceneCut).mock.calls[0]?.[2]
  app?.unmount(); app = undefined; finish(); await settle()
  expect(signal?.aborted).toBe(true)
  expect(deleted).not.toHaveBeenCalled()
  expect(vi.getTimerCount()).toBe(0)
})
it.each(['source_id', 'deleted'] as const)('rejects a deletion response with the wrong %s', async field => {
  vi.mocked(api.deleteSceneCut).mockResolvedValue({ source_id: source.id, deleted: cut().id, [field]: 'f'.repeat(32) })
  askDelete.mockReturnValue(true)
  const deleted = vi.fn<(cut: VideoSceneCut) => void>()
  await mount(deleted); button('[data-scene-delete]').click(); await settle()
  expect(document.querySelector('[data-scene-download]')?.getAttribute('href')).toBe(cut().output_url)
  expect(deleted).not.toHaveBeenCalled()
  expect(document.querySelector('[role=alert]')?.textContent).toContain('Refresh')
})
it('retains the scene after a safe deletion failure', async () => {
  vi.mocked(api.deleteSceneCut).mockRejectedValue(new ApiError('storage_unavailable', 503))
  askDelete.mockReturnValue(true)
  await mount(); button('[data-scene-delete]').click(); await settle()
  expect(document.querySelector('[data-scene-download]')?.getAttribute('href')).toBe(cut().output_url)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('local library')
  expect(button('[data-scene-delete]').disabled).toBe(false)
})
it('does not resurrect a deleted scene from an old in-flight poll', async () => {
  vi.mocked(api.listSceneCuts).mockResolvedValueOnce({ cuts: [cut('cutting')] })
  let finish: (value: { cuts: VideoSceneCut[] }) => void = () => { throw new Error('Uninitialized') }
  vi.mocked(api.listSceneCuts).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  vi.mocked(api.deleteSceneCut).mockResolvedValue({ source_id: source.id, deleted: cut().id })
  askDelete.mockReturnValue(true)
  await mount(); await vi.advanceTimersByTimeAsync(3000)
  const signal = vi.mocked(api.listSceneCuts).mock.calls[1]?.[1]
  button('[data-scene-cancel]').click(); await settle(); button('[data-scene-delete]').click(); await settle()
  finish({ cuts: [cut()] }); await settle(); await vi.advanceTimersByTimeAsync(6000)
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector('[data-scene-download]')).toBeNull()
  expect(api.listSceneCuts).toHaveBeenCalledTimes(2)
})
it('identifies a fractional scene range precisely in deletion confirmation', async () => {
  vi.mocked(api.listSceneCuts).mockResolvedValue({ cuts: [{ ...cut(), start_sec: .125, end_sec: .875, seconds: .75 }] })
  await mount(); button('[data-scene-delete]').click(); await settle()
  expect(askDelete).toHaveBeenCalledWith(expect.stringContaining('0:00.125–0:00.875'))
})
