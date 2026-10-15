// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n, setLocale } from '../../i18n'
import * as music from '../../api/localMusic'
import * as adapters from '../../api/musicLora'
import { ApiError } from '../../api/http'
import type { LocalMusicEngineId } from '../../api/localMusic'
import type { LocalMusicJob, LocalMusicJobs, LocalMusicRequest, SavedTrack } from '../../api/generated'
import LocalMusicPanel from './LocalMusicPanel.vue'
import { pendingMusicAdapter } from '../../composables/generationDrafts'

vi.mock('../../api/localMusic', () => ({ localMusicEngines: vi.fn(), listLocalMusic: vi.fn(), submitLocalMusic: vi.fn(), cancelLocalMusic: vi.fn() }))
vi.mock('../../api/musicLora', () => ({ listMusicAdapters: vi.fn() }))
vi.mock('../../components/shared/StemsPanel.vue', () => ({ default: defineComponent({ props: { trackId: { type: Number, required: true } }, setup: props => () => h('div', `Stems and editor for ${props.trackId}`) }) }))
let app: App | undefined
const engines = ['heartmula', 'stable_audio3'] as const
function request(engine: LocalMusicEngineId = 'heartmula'): LocalMusicRequest {
  return { engine, title: 'Evening', prompt: 'jazz, piano', lyrics: engine === 'stable_audio3' ? '' : '[Verse]\nA quiet evening', duration_seconds: 30, seed: 0, variant: 'small-music' }
}
function job(status: LocalMusicJob['status'] = 'running', engine: LocalMusicEngineId = 'heartmula'): LocalMusicJob {
  return { id: 'a'.repeat(32), status, created_at: '2026-10-14', request: request(engine), track: null, error_code: null }
}
const track: SavedTrack = { id: 42, short_id: 42, model: 'heartmula', title: 'Saved evening', created_at: '2026-10-14', lyrics: '', seed: 0, duration_ms: 30000, wall_ms: 5000, params: {}, filename: 'song.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null }
async function settle() { for (let n = 0; n < 12; n++) await nextTick() }
async function mount(engine: LocalMusicEngineId = 'heartmula') {
  const selected = ref(engine)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }, { path: '/settings', component: { render: () => null } }, { path: '/music/lora', component: { render: () => null } }] })
  await router.push('/')
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(LocalMusicPanel, { engine: selected.value }) }).use(router).use(i18n)
  app.mount(node); await settle()
  return { node, selected }
}
function button(node: HTMLElement, label: string): HTMLButtonElement {
  const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!found) throw new Error(`Missing ${label}`)
  return found
}
async function input(node: HTMLElement, name: string, value: string) {
  const element = node.querySelector(`[name="${name}"]`)
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) throw new Error(`Missing ${name}`)
  element.value = value; element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); await settle()
}
beforeEach(() => {
  pendingMusicAdapter.value = null
  vi.resetAllMocks(); setLocale('en')
  vi.mocked(music.localMusicEngines).mockResolvedValue({ engines: engines.map(id => ({ id, installed: true, weights_ready: true, supported: true, runtime: 'mlx', detail: '' })) })
  vi.mocked(music.listLocalMusic).mockResolvedValue({ jobs: [], history_incomplete: false })
  vi.mocked(music.submitLocalMusic).mockResolvedValue(job('queued'))
  vi.mocked(music.cancelLocalMusic).mockResolvedValue(job('cancelled'))
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [{ id: 'b'.repeat(32), engine: 'stable_audio3', target: 'small-music', name: 'Piano style', sha256: 'c'.repeat(64), created_at: 'now', source_job_id: null }], history_incomplete: false })
})

it('applies a trained adapter draft on entry without automatically generating', async () => {
  pendingMusicAdapter.value = { engine: 'stable_audio3', target: 'small-music', selection: { id: 'b'.repeat(32), strength: 1 } }
  const { node } = await mount('stable_audio3')
  expect(pendingMusicAdapter.value).toBeNull()
  expect(music.submitLocalMusic).not.toHaveBeenCalled()
  await input(node, 'prompt', 'A piano melody')
  button(node, 'Generate music').click(); await settle()
  expect(music.submitLocalMusic).toHaveBeenCalledWith(expect.objectContaining({ adapter: { id: 'b'.repeat(32), strength: 1 } }), expect.any(AbortSignal))
})

it('describes the corrected Heart adapter runtime independently of MLX installation', async () => {
  vi.mocked(music.localMusicEngines).mockResolvedValue({ engines: [{ id: 'heartmula', installed: false, weights_ready: false, supported: true, runtime: 'Community HeartMuLa MLX port', detail: '', adapter_runtime_ready: true }] })
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [{ id: 'd'.repeat(32), engine: 'heartmula', target: 'heartmula-3b', name: 'Heart style', sha256: 'c'.repeat(64), created_at: 'now', source_job_id: null }] })
  pendingMusicAdapter.value = { engine: 'heartmula', target: 'heartmula-3b', selection: { id: 'd'.repeat(32), strength: 1 } }
  const { node } = await mount('heartmula')
  expect(node.querySelector('header')?.textContent).toContain('Corrected HeartMuLa PyTorch adapter runtime')
  expect(node.querySelector('header')?.textContent).not.toContain('Community HeartMuLa MLX port')
  expect(node.querySelector('header')?.textContent).not.toContain(i18n.global.t('localMusic.missingEngine'))
  await input(node, 'prompt', 'Jazz')
  expect(button(node, 'Generate music').disabled).toBe(false)
})

it('retains a preview adapter when its draft switches the current Stable variant', async () => {
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [{ id: 'd'.repeat(32), engine: 'stable_audio3', target: 'medium', name: 'Medium style', sha256: 'c'.repeat(64), created_at: 'now', source_job_id: null }] })
  const { node } = await mount('stable_audio3')
  pendingMusicAdapter.value = { engine: 'stable_audio3', target: 'medium', selection: { id: 'd'.repeat(32), strength: 1 } }
  await settle()
  await input(node, 'prompt', 'A piano melody')
  button(node, 'Generate music').click(); await settle()
  expect(music.submitLocalMusic).toHaveBeenCalledWith(expect.objectContaining({ variant: 'medium', adapter: { id: 'd'.repeat(32), strength: 1 } }), expect.any(AbortSignal))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })

it('shows setup readiness and does not automatically generate or download weights', async () => {
  vi.mocked(music.localMusicEngines).mockResolvedValue({ engines: [{ id: 'heartmula', installed: true, weights_ready: false, supported: true, runtime: 'mlx', detail: '/private/weights' }] })
  const { node } = await mount()
  expect(node.querySelector('a[href="/settings#module-heartmula"]')).not.toBeNull()
  expect(node.textContent).toContain('Model weights are missing')
  expect(node.textContent).not.toContain('/private/weights')
  expect(button(node, 'Generate music').disabled).toBe(true)
  expect(music.submitLocalMusic).not.toHaveBeenCalled()
})

it('submits lyrics, tags and bounded settings only through an explicit action', async () => {
  const { node } = await mount()
  expect(node.textContent).toContain('Maximum duration (seconds)')
  await input(node, 'prompt', 'jazz, piano')
  await input(node, 'lyrics', '[Verse]\nA quiet evening')
  await input(node, 'title', 'Evening')
  button(node, 'Generate music').click(); await settle()
  expect(music.submitLocalMusic).toHaveBeenCalledExactlyOnceWith(request(), expect.any(AbortSignal))
  expect(node.textContent).toContain('Queued')
  expect(node.textContent).not.toMatch(/\d+%/)
})

it('recovers persisted outputs on reload and makes them playable, downloadable and available to the editor', async () => {
  vi.mocked(music.listLocalMusic).mockResolvedValue({ jobs: [{ ...job('done'), track }, job('running', 'stable_audio3')], history_incomplete: true })
  const { node } = await mount()
  expect(node.textContent).toContain('Saved evening')
  expect(node.querySelector('audio')?.getAttribute('src')).toBe(track.audio_url)
  expect(node.querySelector('a[download]')?.getAttribute('href')).toBe(track.audio_url)
  expect(node.textContent).toContain('Stems and editor for 42')
  expect(node.textContent).toContain('Some history could not be read')
  expect(node.textContent).not.toContain('Generating')
  expect(music.submitLocalMusic).not.toHaveBeenCalled()
})

it('cancels an owned running job and stops polling when it becomes terminal', async () => {
  vi.useFakeTimers()
  vi.mocked(music.listLocalMusic).mockResolvedValue({ jobs: [job()], history_incomplete: false })
  const { node } = await mount()
  button(node, 'Cancel generation').click(); await settle()
  expect(music.cancelLocalMusic).toHaveBeenCalledExactlyOnceWith(job().id, expect.any(AbortSignal))
  expect(node.textContent).toContain('Cancelled')
  await vi.advanceTimersByTimeAsync(5000); await settle()
  expect(music.listLocalMusic).toHaveBeenCalledTimes(1)
})

it('keeps polling an active job after a transient failure and shows safe translated errors', async () => {
  vi.useFakeTimers()
  vi.mocked(music.listLocalMusic).mockResolvedValueOnce({ jobs: [job()], history_incomplete: false }).mockRejectedValueOnce(new Error('/private/token')).mockResolvedValue({ jobs: [job('failed')], history_incomplete: false })
  const { node } = await mount()
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(node.textContent).not.toContain('/private/token')
  expect(node.textContent).toContain('Could not refresh local music')
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(music.listLocalMusic).toHaveBeenCalledTimes(3)
  expect(node.textContent).toContain('Generation failed')
})

it('stops polling and aborts pending requests on teardown without cancelling backend work', async () => {
  vi.useFakeTimers()
  let finish: ((value: LocalMusicJobs) => void) | undefined
  vi.mocked(music.listLocalMusic).mockReturnValueOnce(Promise.resolve({ jobs: [job()], history_incomplete: false })).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  await mount(); await vi.advanceTimersByTimeAsync(2500)
  const signal = vi.mocked(music.listLocalMusic).mock.calls.at(-1)?.[0]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  finish?.({ jobs: [job()], history_incomplete: false }); await settle()
  await vi.advanceTimersByTimeAsync(10000)
  expect(music.listLocalMusic).toHaveBeenCalledTimes(2)
  expect(music.cancelLocalMusic).not.toHaveBeenCalled()
})

it('discards a late submission after switching engines and restores the new engine history', async () => {
  vi.useFakeTimers()
  let finish: ((value: LocalMusicJob) => void) | undefined
  vi.mocked(music.submitLocalMusic).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node, selected } = await mount()
  await input(node, 'prompt', 'jazz, piano'); await input(node, 'lyrics', '[Verse]\nA quiet evening')
  button(node, 'Generate music').click(); await settle()
  const signal = vi.mocked(music.submitLocalMusic).mock.calls[0]?.[1]
  selected.value = 'stable_audio3'; await settle()
  expect(signal?.aborted).toBe(true)
  finish?.(job('queued')); await settle()
  await vi.advanceTimersByTimeAsync(5000); await settle()
  expect(node.textContent).toContain('Stable Audio 3')
  expect(node.textContent).not.toContain('Queued')
  expect(node.querySelector('[name="lyrics"]')).toBeNull()
  expect(music.listLocalMusic).toHaveBeenCalledTimes(2)
})

it('updates Stable Audio duration bounds with the selected model and sends no lyrics', async () => {
  const { node } = await mount('stable_audio3')
  expect(node.textContent).not.toContain('Maximum duration (seconds)')
  await input(node, 'prompt', 'A quiet instrumental jazz piece')
  await input(node, 'duration_seconds', '200')
  expect(button(node, 'Generate music').disabled).toBe(true)
  await input(node, 'variant', 'medium')
  expect(button(node, 'Generate music').disabled).toBe(false)
  button(node, 'Generate music').click(); await settle()
  expect(music.submitLocalMusic).toHaveBeenCalledWith(expect.objectContaining({ engine: 'stable_audio3', variant: 'medium', duration_seconds: 200, lyrics: '' }), expect.any(AbortSignal))
})

it('reports a safe submission failure and permits an explicit history refresh', async () => {
  vi.mocked(music.submitLocalMusic).mockRejectedValue(new Error('/private/secret'))
  const { node } = await mount()
  await input(node, 'prompt', 'jazz, piano'); await input(node, 'lyrics', '[Verse]\nEvening')
  button(node, 'Generate music').click(); await settle()
  expect(node.textContent).toContain('Refresh history before trying again')
  expect(node.textContent).not.toContain('/private/secret')
  button(node, 'Refresh history').click(); await settle()
  expect(music.listLocalMusic).toHaveBeenCalledTimes(2)
})

it.each([
  ['local_music_weights_missing', 'Model weights for the selected model are missing'],
  ['music_lora_adapter_not_found', 'The selected adapter is unavailable'],
  ['music_lora_adapter_changed', 'The adapter file changed or could not be verified'],
  ['music_lora_target_mismatch', 'This checkpoint is not a supported native adapter'],
  ['music_lora_format_invalid', 'This checkpoint is not a supported native adapter'],
  ['local_music_engine_not_installed', 'Engine runtime is not installed'],
  ['local_music_runtime_unsupported', 'This engine is not supported on the current platform'],
  ['local_music_busy', 'Another generation or setup operation is running'],
  ['module_setup_busy', 'Another generation or setup operation is running'],
])('translates the %s action error without exposing backend codes', async (code, message) => {
  vi.mocked(music.submitLocalMusic).mockRejectedValue(new ApiError(code, 409))
  const { node } = await mount()
  await input(node, 'prompt', 'jazz, piano')
  button(node, 'Generate music').click(); await settle()
  expect(node.querySelector('[role="alert"]')?.textContent).toContain(message)
  expect(node.textContent).not.toContain(code)
  expect(node.querySelector('a[href="/settings#module-heartmula"]')).not.toBeNull()
})

it('explains missing weights for the selected Stable model when a job fails', async () => {
  vi.mocked(music.listLocalMusic).mockResolvedValue({ jobs: [{ ...job('failed', 'stable_audio3'), error_code: 'local_music_weights_missing' }], history_incomplete: false })
  const { node } = await mount('stable_audio3')
  expect(node.textContent).toContain('Model weights for the selected model are missing')
  expect(node.textContent).not.toContain('local_music_weights_missing')
})

it('submits a compatible Stable adapter selection and clears it when switching base models', async () => {
  const { node } = await mount('stable_audio3')
  const selector = node.querySelector('select[name="adapter-small-music"]')
  expect(selector).not.toBeNull()
  if (!(selector instanceof HTMLSelectElement)) throw new Error('Missing Stable adapter selector')
  selector.value = 'b'.repeat(32); selector.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  await input(node, 'prompt', 'Piano jazz')
  expect(button(node, 'Generate music').disabled).toBe(false)
  expect(node.querySelector('form')?.checkValidity()).toBe(true)
  button(node, 'Generate music').click(); await settle()
  expect(music.submitLocalMusic).toHaveBeenCalledWith(expect.objectContaining({ adapter: { id: 'b'.repeat(32), strength: 1 } }), expect.any(AbortSignal))
  vi.mocked(music.listLocalMusic).mockResolvedValue({ jobs: [], history_incomplete: false })
  button(node, 'Refresh history').click(); await settle()
  await input(node, 'variant', 'medium')
  button(node, 'Generate music').click(); await settle()
  expect(vi.mocked(music.submitLocalMusic).mock.calls.at(-1)?.[0].adapter).toBeNull()
})

it('offers only matching HeartMuLa adapters', async () => {
  const { node } = await mount('heartmula')
  expect(node.querySelector('select[name="adapter-heartmula-3b"]')).not.toBeNull()
  expect(node.querySelector('select[name="adapter-small-music"]')).toBeNull()
})

it('blocks even a programmatic Stable form submission when adapter strength is invalid', async () => {
  const { node } = await mount('stable_audio3')
  await input(node, 'prompt', 'Piano jazz')
  const selector = node.querySelector('select[name="adapter-small-music"]')
  if (!(selector instanceof HTMLSelectElement)) throw new Error('Missing adapter selector')
  selector.value = 'b'.repeat(32); selector.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  const strength = selector.parentElement?.parentElement?.querySelector('input')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing adapter strength')
  strength.value = ''; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(button(node, 'Generate music').disabled).toBe(true)
  node.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
  expect(music.submitLocalMusic).not.toHaveBeenCalled()
  strength.value = '0.5'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  button(node, 'Generate music').click(); await settle()
  expect(music.submitLocalMusic).toHaveBeenCalledWith(expect.objectContaining({ adapter: { id: 'b'.repeat(32), strength: 0.5 } }), expect.any(AbortSignal))
})

it('uses a HeartMuLa adapter only when its separate training runtime is prepared', async () => {
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [{ id: 'b'.repeat(32), engine: 'heartmula', target: 'heartmula-3b', name: 'Warm voice', sha256: 'c'.repeat(64), created_at: 'now' }] })
  vi.mocked(music.localMusicEngines).mockResolvedValue({ engines: [{ id: 'heartmula', installed: true, weights_ready: true, supported: true, runtime: 'mlx', detail: '', adapter_runtime_ready: true }] })
  const { node } = await mount()
  await input(node, 'prompt', 'Warm piano')
  await input(node, 'adapter-heartmula-3b', 'b'.repeat(32))
  button(node, 'Generate music').click(); await settle()
  expect(music.submitLocalMusic).toHaveBeenCalledWith(expect.objectContaining({ engine: 'heartmula', adapter: { id: 'b'.repeat(32), strength: 1 } }), expect.any(AbortSignal))
})
