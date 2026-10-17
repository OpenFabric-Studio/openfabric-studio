// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n, setLocale } from '../../i18n'
import MusicQueuePage from './MusicQueuePage.vue'
import * as adapters from '../../api/musicLora'
import { ApiError } from '../../api/http'
import * as terms from '../../api/musicTerms'
vi.mock('../../api/musicTerms', () => ({ getMusicTerms: vi.fn(), acceptMusicTerms: vi.fn(), updateMusicTermsSettings: vi.fn() }))
import { pendingAceSongSource, pendingMusicConditioning } from '../../composables/generationDrafts'
import { emptyAceSettings } from '../../composables/generationSnapshots'
import type { MusicQueueBatch, MusicQueueCatalog, MusicQueueItem, MusicQueuePreview, MusicQueuePreviewRequest, MusicQueueSnapshot, SavedTrack } from '../../api/generated'

const api = vi.hoisted(() => ({ musicQueueCatalog: vi.fn(), getMusicQueue: vi.fn(), previewMusicQueue: vi.fn(), enqueueMusicQueue: vi.fn(), controlMusicQueue: vi.fn(), reorderMusicQueue: vi.fn(), retryMusicQueue: vi.fn(), cancelMusicQueueBatch: vi.fn() }))
vi.mock('../../api/musicQueue', () => ({ ...api }))
vi.mock('../../api/songFromBeat', () => ({ songFromBeatCapabilities: vi.fn().mockResolvedValue({ modes: [] }) }))
vi.mock('../../api/modules', () => ({ listModuleJobs: vi.fn().mockResolvedValue([]) }))
vi.mock('../../api/musicLora', () => ({ listMusicAdapters: vi.fn() }))
vi.mock('../../components/shared/TrackAudioVersions.vue', () => ({ default: defineComponent({ props: ['trackId', 'fallbackAudioUrl'], emits: ['playing', 'processing'], setup: (props, { emit }) => () => h('audio', { src: props.fallbackAudioUrl, 'data-track': props.trackId, onPlay: () => emit('playing', true), onPause: () => emit('playing', false) }) }) }))
vi.mock('../../components/shared/FavoriteTrackButton.vue', () => ({ default: defineComponent({ props: ['trackId'], setup: props => () => h('button', `Favourite ${props.trackId}`) }) }))
vi.mock('../../components/shared/StemsPanel.vue', () => ({ default: defineComponent({ props: ['trackId'], setup: props => () => h('div', `Stems and editor for ${props.trackId}`) }) }))

const conditioning = { mode: 'audio_to_audio', source: { kind: 'version', track_id: 7, version_id: 'e'.repeat(32) }, source_sha256: 'd'.repeat(64), noise_level: .6 } as const
vi.mock('./MusicConditioningControls.vue', () => ({ default: defineComponent({
  props: ['request', 'disabled', 'sourceDraft'], emits: ['change', 'validity', 'draftApplied'],
  setup: (props, { emit }) => () => h('section', { 'data-reference': true }, [
    h('button', { type: 'button', disabled: props.disabled, onClick: () => { emit('change', conditioning); emit('validity', true); emit('draftApplied') } }, 'Review fixture reference'),
    h('button', { type: 'button', onClick: () => emit('validity', false) }, 'Invalidate reference'),
  ]),
}) }))

let app: App | undefined
const engines = ['ace_step', 'yue2', 'stable_audio3'] as const
const catalog: MusicQueueCatalog = { engines: engines.map(engine => ({ engine, supported: true, installed: true, weights_ready: true, managed_status: engine === 'stable_audio3' ? 'one_shot' : 'stopped', owned_instance: null, ready: true, error_code: '', setup_href: `/settings#module-${engine}` })), adapters: [] }
const original = { title: 'Evening', description: 'Warm piano', lyrics: '', instrumental: true, duration_seconds: 120, bpm: null, key: '', language: '' }
function preview(request: MusicQueuePreviewRequest = { brief: original, selections: [{ engine: 'stable_audio3', variations: 1 }] }): MusicQueuePreview {
  const readiness = catalog.engines.find(model => model.engine === 'stable_audio3')
  if (!readiness) throw new Error('Missing fixture readiness')
  const selection = request.selections[0]
  if (!selection) throw new Error('Missing fixture selection')
  return { ...request, models: [{ selection, readiness, compatible: true, effective_duration_seconds: 120, duration_mode: 'target', adjustments: [], warnings: ['music_queue_one_shot_reload'], adapters: [] }], items: [{ engine: 'stable_audio3', variation_index: 1, payload: { engine: 'stable_audio3', request: { engine: 'stable_audio3', prompt: request.brief.description, lyrics: '', duration_seconds: 120, seed: 0, variant: 'small-music' } }, adapters: [] }], total_items: 1, can_enqueue: true, fingerprint: 'f'.repeat(64) }
}
function batch(id = 'a'.repeat(32), status: MusicQueueItem['status'] = 'queued'): MusicQueueBatch {
  const plan = preview(), item = plan.items[0]
  if (!item) throw new Error('Missing fixture item')
  return { id, created_at: '2026-10-14', brief: plan.brief, selections: plan.selections, models: plan.models, preview_fingerprint: plan.fingerprint, items: [{ ...item, id: id === 'a'.repeat(32) ? 'b'.repeat(32) : 'd'.repeat(32), batch_id: id, resolved_seed: 42, child_job_id: null, status, tracks: [], error_code: '', attempt: 1, retry_of_item_id: null }] }
}
function snapshot(batches: MusicQueueBatch[] = [], revision = 1, status: MusicQueueSnapshot['status'] = 'idle'): MusicQueueSnapshot { return { revision, status, active_item_id: null, error_code: '', batches } }
async function settle() { for (let n = 0; n < 12; n++) await nextTick() }
async function mount() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/music', component: { render: () => null } }, { path: '/settings', component: { render: () => null } }, { path: '/music/lora', component: { render: () => null } }, { path: '/editor/new', component: { render: () => null } }] })
  await router.push('/music')
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(MusicQueuePage) }).use(createPinia()).use(router).use(i18n)
  app.mount(node); await settle(); return { node, router }
}
function button(node: ParentNode, label: string): HTMLButtonElement {
  const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label || item.getAttribute('aria-label') === label)
  if (!found) throw new Error(`Missing ${label}`)
  return found
}
async function input(node: HTMLElement, name: string, value: string) {
  const element = node.querySelector(`[name="${name}"]`)
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) throw new Error(`Missing ${name}`)
  element.value = value; element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); await settle()
}
async function select(node: HTMLElement, engine: typeof engines[number]) {
  const element = node.querySelector<HTMLInputElement>(`input[name="model_${engine}"]`)
  if (!element) throw new Error(`Missing model ${engine}`)
  element.click(); await settle()
}
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); setLocale('en'); pendingMusicConditioning.value = null; pendingAceSongSource.value = null
  api.musicQueueCatalog.mockResolvedValue(catalog)
  api.getMusicQueue.mockResolvedValue(snapshot())
  api.previewMusicQueue.mockReturnValue(new Promise(() => {}))
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [], history_incomplete: false })
})
beforeEach(() => { vi.mocked(terms.getMusicTerms).mockImplementation(async (engine, operation) => ({ engine, operation, revision: 0, noncommercial_enabled: false, requirements: [], allowed: true })) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })

it('shows actual model readiness without starting or enqueuing work on entry', async () => {
  api.musicQueueCatalog.mockResolvedValue({ ...catalog, engines: catalog.engines.map(model => model.engine === 'stable_audio3' ? { ...model, ready: false, weights_ready: false } : model) })
  const { node } = await mount()
  expect(node.textContent).toContain('The brief')
  expect(node.textContent).toContain('Your generation plan')
  expect(node.textContent).toContain('Model weights are missing')
  expect(node.querySelector('a[href="/settings#module-stable_audio3"]')).not.toBeNull()
  expect(node.querySelector<HTMLInputElement>('input[name="model_stable_audio3"]')?.disabled).toBe(true)
  expect(api.controlMusicQueue).not.toHaveBeenCalled(); expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
  expect(api.previewMusicQueue).not.toHaveBeenCalled()
})

it('previews selection order and a separate variation count for every model without starting work', async () => {
  const { node } = await mount()
  button(node, 'Instrumental').click(); await settle()
  await input(node, 'description', 'Warm piano and brushed drums')
  await select(node, 'stable_audio3'); await select(node, 'ace_step')
  await input(node, 'variations_stable_audio3', '3'); await input(node, 'variations_ace_step', '2')
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenCalledTimes(1)
  expect(api.previewMusicQueue).toHaveBeenCalledWith(expect.objectContaining({ brief: expect.objectContaining({ description: 'Warm piano and brushed drums' }), selections: [expect.objectContaining({ engine: 'stable_audio3', variations: 3 }), expect.objectContaining({ engine: 'ace_step', variations: 2 })] }), expect.any(AbortSignal))
  expect(node.querySelector('[data-variation-total]')?.textContent).toContain('5')
  expect(api.controlMusicQueue).not.toHaveBeenCalled(); expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
})

it('explains incompatible vocal models and preserves lyrics while changing brief mode', async () => {
  const { node } = await mount()
  expect(node.querySelector<HTMLInputElement>('input[name="model_stable_audio3"]')?.disabled).toBe(true)
  expect(node.textContent).toContain('Instrumental only')
  await input(node, 'lyrics', '[Verse]\nKeep this original text')
  await select(node, 'yue2')
  button(node, 'Instrumental').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[name="model_yue2"]')?.disabled).toBe(true)
  expect(node.textContent).toContain('YuE requires lyrics')
  button(node, 'With vocals').click(); await settle()
  expect(node.querySelector<HTMLTextAreaElement>('[name="lyrics"]')?.value).toBe('[Verse]\nKeep this original text')
})

it('does not request a preview for invalid counts and aborts stale previews immediately', async () => {
  const { node } = await mount()
  await input(node, 'description', 'Warm piano'); await select(node, 'ace_step')
  await vi.advanceTimersByTimeAsync(350); await settle()
  const signal: unknown = api.previewMusicQueue.mock.calls[0]?.[1]
  await input(node, 'variations_ace_step', '9')
  expect(signal instanceof AbortSignal && signal.aborted).toBe(true)
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenCalledTimes(1)
})

async function compose(node: HTMLElement) {
  button(node, 'Instrumental').click(); await settle()
  await input(node, 'title', 'Evening'); await input(node, 'description', 'Warm piano'); await select(node, 'stable_audio3')
  await vi.advanceTimersByTimeAsync(350); await settle()
}

it('requires explicit terms acceptance before adding a restricted model to the queue', async () => {
  const before = { engine: 'stable_audio3', operation: 'generation', revision: 0, noncommercial_enabled: false, allowed: false,
    requirements: [{ component_id: 'weights', name: 'Weights', scope: 'model', restriction: 'conditional', source_url: 'https://example.org/terms', terms_digest: 'a'.repeat(64), acknowledged: false }] } satisfies Awaited<ReturnType<typeof terms.getMusicTerms>>
  vi.mocked(terms.getMusicTerms).mockResolvedValue(before)
  vi.mocked(terms.acceptMusicTerms).mockResolvedValue({ ...before, allowed: true, revision: 1, requirements: before.requirements.map(row => ({ ...row, acknowledged: true })) })
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => preview(request))
  const { node } = await mount(); await compose(node)
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(true)
  const review = node.querySelector('section[aria-label="Publisher terms"] input')
  if (!(review instanceof HTMLInputElement)) throw new Error('Missing terms review')
  review.click(); await settle(); button(node, 'Accept reviewed terms').click(); await settle()
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(false)
  expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
})

it('enqueues a verified immutable plan with one client key and leaves starting to the user', async () => {
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => preview(request))
  api.enqueueMusicQueue.mockResolvedValue(snapshot([batch()], 2))
  const { node } = await mount(); await compose(node)
  expect(node.textContent).toContain('Current runtime reloads for each variation')
  button(node, 'Add 1 variation to queue').click(); button(node, 'Add 1 variation to queue').click(); await settle()
  expect(api.enqueueMusicQueue).toHaveBeenCalledTimes(1)
  expect(api.enqueueMusicQueue).toHaveBeenCalledWith(expect.objectContaining({ revision: 1, submission_key: expect.any(String), preview_fingerprint: 'f'.repeat(64), brief: expect.objectContaining({ title: 'Evening', description: 'Warm piano' }) }), expect.any(AbortSignal))
  await input(node, 'title', 'Different idea'); await input(node, 'description', 'Loud brass')
  expect(node.querySelector('[data-queue-batch]')?.textContent).toContain('Evening')
  expect(node.querySelector('[data-queue-batch]')?.textContent).toContain('Warm piano')
  expect(api.controlMusicQueue).not.toHaveBeenCalled()
})

it('keeps the same submission key after an ambiguous network failure and shows a safe error', async () => {
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => preview(request))
  api.enqueueMusicQueue.mockRejectedValueOnce(new Error('/private/token-secret')).mockResolvedValue(snapshot([batch()], 2))
  const { node } = await mount(); await compose(node)
  button(node, 'Add 1 variation to queue').click(); await settle()
  expect(node.textContent).not.toContain('token-secret')
  expect(node.querySelector('[role="alert"]')?.textContent).toContain('Could not update the queue')
  api.musicQueueCatalog.mockResolvedValueOnce({ ...catalog, engines: catalog.engines.map(value => ({ ...value })) })
  button(node, 'Refresh setup').click(); await settle(); await vi.advanceTimersByTimeAsync(350); await settle()
  button(node, 'Add 1 variation to queue').click(); await settle()
  expect(api.enqueueMusicQueue.mock.calls[0]?.[0].submission_key).toBe(api.enqueueMusicQueue.mock.calls[1]?.[0].submission_key)
})

it('ignores a late preview after the brief changes and blocks enqueue until the new preview returns', async () => {
  let finish: ((value: MusicQueuePreview) => void) | undefined
  api.previewMusicQueue.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { node } = await mount(); await compose(node)
  await input(node, 'description', 'A newer brief')
  finish?.(preview()); await settle()
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(true)
  expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
})

it('restores a paused queue after reload and resumes only through a revisioned explicit action', async () => {
  api.getMusicQueue.mockResolvedValue(snapshot([batch()], 7, 'paused'))
  api.controlMusicQueue.mockResolvedValue({ ...snapshot([batch('a'.repeat(32), 'running')], 8, 'running'), active_item_id: 'b'.repeat(32) })
  const { node } = await mount()
  expect(node.textContent).toContain('Evening'); expect(node.textContent).toContain('Paused')
  await vi.advanceTimersByTimeAsync(5000); await settle()
  expect(api.controlMusicQueue).not.toHaveBeenCalled(); expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
  button(node, 'Resume queue').click(); await settle()
  expect(api.controlMusicQueue).toHaveBeenCalledExactlyOnceWith({ revision: 7, action: 'start' }, expect.any(AbortSignal))
  button(node, 'Pause after this variation').click(); await settle()
  expect(api.controlMusicQueue).toHaveBeenLastCalledWith({ revision: 8, action: 'pause' }, expect.any(AbortSignal))
})

it('moves and cancels only unstarted briefs using the observed revision', async () => {
  const first = batch(), second = { ...batch('c'.repeat(32)), brief: { ...original, title: 'Second idea' } }
  api.getMusicQueue.mockResolvedValue(snapshot([first, second], 3))
  api.reorderMusicQueue.mockResolvedValue(snapshot([second, first], 4))
  api.cancelMusicQueueBatch.mockResolvedValue(snapshot([second, batch('a'.repeat(32), 'cancelled')], 5))
  const { node } = await mount()
  button(node, 'Move Second idea earlier').click(); await settle()
  expect(api.reorderMusicQueue).toHaveBeenCalledExactlyOnceWith({ revision: 3, batch_ids: [second.id, first.id] }, expect.any(AbortSignal))
  button(node, 'Cancel Evening').click(); await settle()
  expect(api.cancelMusicQueueBatch).toHaveBeenCalledExactlyOnceWith({ revision: 4, batch_id: first.id }, expect.any(AbortSignal))
})

it('retains saved results with library playback, favourite and editor actions', async () => {
  const track: SavedTrack = { id: 42, short_id: 42, model: 'stable_audio3', title: 'Saved evening', created_at: '2026-10-14', lyrics: '', seed: 42, duration_ms: 120000, wall_ms: 5000, params: {}, filename: 'song.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null }
  const saved = batch('a'.repeat(32), 'done'), item = saved.items[0]
  if (!item) throw new Error('Missing fixture item')
  saved.items = [{ ...item, tracks: [track] }]
  api.getMusicQueue.mockResolvedValue(snapshot([saved], 4))
  const { node } = await mount()
  button(node, 'Results').click(); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe(track.audio_url)
  expect(node.textContent).toContain('Saved evening'); expect(node.textContent).toContain('Favourite 42'); expect(node.textContent).toContain('Stems and editor for 42')
  expect(api.controlMusicQueue).not.toHaveBeenCalled()
})

it('records explicit inference retry and never automatically resubmits failed items', async () => {
  const failed = batch('a'.repeat(32), 'failed')
  api.getMusicQueue.mockResolvedValue(snapshot([failed], 9, 'paused')); api.retryMusicQueue.mockResolvedValue(snapshot([failed], 10, 'paused'))
  const { node } = await mount()
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(api.retryMusicQueue).not.toHaveBeenCalled()
  button(node, 'Retry generation').click(); await settle()
  expect(api.retryMusicQueue).toHaveBeenCalledExactlyOnceWith({ revision: 9, item_id: 'b'.repeat(32), mode: 'inference' }, expect.any(AbortSignal))
})

it.each(['save_failed', 'local_music_save_failed'])('offers %s publication retry against the existing child', async error_code => {
  const failed = batch('a'.repeat(32), 'failed'), item = failed.items[0]
  if (!item) throw new Error('Missing fixture item')
  failed.items = [{ ...item, child_job_id: 'native-child', error_code }]
  api.getMusicQueue.mockResolvedValue(snapshot([failed], 9, 'paused')); api.retryMusicQueue.mockResolvedValue(snapshot([failed], 10, 'paused'))
  const { node } = await mount(); button(node, 'Retry saving').click(); await settle()
  expect(api.retryMusicQueue).toHaveBeenCalledExactlyOnceWith({ revision: 9, item_id: 'b'.repeat(32), mode: 'save' }, expect.any(AbortSignal))
})

it('cancels only the observed active variation and retains saved outputs', async () => {
  const active = batch('a'.repeat(32), 'running')
  api.getMusicQueue.mockResolvedValue({ ...snapshot([active], 4, 'running'), active_item_id: 'b'.repeat(32) })
  api.controlMusicQueue.mockResolvedValue(snapshot([batch('a'.repeat(32), 'cancelled')], 5, 'paused'))
  const { node } = await mount(); button(node, 'Cancel current variation').click(); await settle()
  expect(api.controlMusicQueue).toHaveBeenCalledExactlyOnceWith({ revision: 4, action: 'cancel_active' }, expect.any(AbortSignal))
  expect(api.retryMusicQueue).not.toHaveBeenCalled()
})

it('rechecks blocked cleanup explicitly before enabling Resume and suppresses duplicate checks', async () => {
  let finish: ((value: MusicQueueSnapshot) => void) | undefined
  api.getMusicQueue.mockResolvedValue({ ...snapshot([batch()], 6, 'blocked'), error_code: 'music_queue_cleanup_failed' })
  api.controlMusicQueue.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node } = await mount()
  expect(button(node, 'Start queue').disabled).toBe(true)
  const cleanup = button(node, 'Retry cleanup'); cleanup.click(); cleanup.click(); await settle()
  expect(cleanup.disabled).toBe(true)
  expect(api.controlMusicQueue).toHaveBeenCalledExactlyOnceWith({ revision: 6, action: 'cancel_active' }, expect.any(AbortSignal))
  expect(button(node, 'Start queue').disabled).toBe(true)
  finish?.(snapshot([batch()], 7, 'paused')); await settle()
  expect(button(node, 'Resume queue').disabled).toBe(false)
  expect([...node.querySelectorAll('button')].some(value => value.textContent?.trim() === 'Retry cleanup')).toBe(false)
})

it('keeps blocked cleanup visible when the server cannot verify release', async () => {
  api.getMusicQueue.mockResolvedValue({ ...snapshot([batch()], 6, 'blocked'), error_code: 'music_queue_cleanup_failed' })
  api.controlMusicQueue.mockRejectedValue(new ApiError('music_queue_cleanup_failed', 503))
  const { node } = await mount(); button(node, 'Retry cleanup').click(); await settle()
  expect(button(node, 'Start queue').disabled).toBe(true)
  expect(button(node, 'Retry cleanup').disabled).toBe(false)
  expect(node.textContent).toContain('The previous model could not be safely released')
})

it('supports arrow and Home keys on the workspace tabs', async () => {
  const { node } = await mount(), create = button(node, 'Create & queue')
  create.focus(); create.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true })); await settle()
  expect(document.activeElement).toBe(button(node, 'Results'))
  expect(button(node, 'Results').getAttribute('aria-selected')).toBe('true')
  document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true })); await settle()
  expect(document.activeElement).toBe(create)
})

it('refreshes a stale revision without exposing backend details or replaying the action', async () => {
  api.getMusicQueue.mockResolvedValueOnce(snapshot([batch()], 3)).mockResolvedValue(snapshot([batch()], 5, 'paused'))
  api.controlMusicQueue.mockRejectedValue(new ApiError('music_queue_revision_conflict', 409))
  const { node } = await mount(); button(node, 'Start queue').click(); await settle()
  expect(node.textContent).toContain('The queue changed in another window')
  expect(api.getMusicQueue).toHaveBeenCalledTimes(2)
  expect(api.controlMusicQueue).toHaveBeenCalledTimes(1)
})

it('explains a busy accelerator without marking pending work as failed', async () => {
  api.getMusicQueue.mockResolvedValue(snapshot([batch()], 3))
  api.controlMusicQueue.mockRejectedValue(new ApiError('native_model_busy', 409))
  const { node } = await mount(); button(node, 'Start queue').click(); await settle()
  expect(node.querySelector('[role="alert"]')?.textContent).toContain('Another generation or setup operation is running')
  expect(node.querySelector('[data-queue-batch]')?.textContent).toContain('Queued')
})

it.each(['ace_lora_selection_mismatch', 'ace_lora_status_unavailable'])('explains ACE adapter preflight failure %s before another queue attempt', async error_code => {
  api.getMusicQueue.mockResolvedValue({ ...snapshot([batch()], 3, 'blocked'), error_code })
  const { node } = await mount()
  expect(node.querySelector('[role="alert"]')?.textContent).toBe(i18n.global.t(`aceGen.loraErrors.${error_code}`))
  expect(api.controlMusicQueue).not.toHaveBeenCalled()
})

const savedTrack: SavedTrack = { id: 42, short_id: 42, model: 'stable_audio3', title: 'Saved evening', created_at: '2026-10-14', lyrics: '', seed: 42, duration_ms: 120000, wall_ms: 5000, params: {}, filename: 'song.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null }
function successfulReplacement() {
  const value = batch('a'.repeat(32), 'failed'), prior = value.items[0]
  if (!prior) throw new Error('Missing fixture item')
  const replacement: MusicQueueItem = { ...prior, id: 'c'.repeat(32), status: 'done', attempt: 2, retry_of_item_id: prior.id, tracks: [savedTrack] }
  value.items = [prior, replacement]
  return value
}

it('removes a successfully retried brief from the queue and keeps every attempt in Results history', async () => {
  const value = successfulReplacement()
  api.getMusicQueue.mockResolvedValue(snapshot([value], 4))
  const { node } = await mount()
  expect(node.querySelectorAll('[data-queue-batch]')).toHaveLength(0)
  expect([...node.querySelectorAll('button')].some(control => control.textContent?.trim() === 'Retry generation')).toBe(false)
  button(node, 'Results').click(); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe(savedTrack.audio_url)
  const history = node.querySelector('[data-attempt-history]')
  expect(history?.textContent).toContain('Attempt 1'); expect(history?.textContent).toContain('Failed')
  expect(history?.textContent).toContain('Attempt 2'); expect(history?.textContent).toContain('Saved')
})

it('keeps unfinished models queued with current variation counts and no retry on superseded attempts', async () => {
  const value = successfulReplacement(), prior = value.items[0]
  if (!prior) throw new Error('Missing fixture item')
  value.selections.push({ engine: 'ace_step', variations: 1 })
  value.items.push({ ...prior, id: 'e'.repeat(32), engine: 'ace_step', status: 'queued', payload: { engine: 'ace_step', request: emptyAceSettings() } })
  api.getMusicQueue.mockResolvedValue(snapshot([value], 4))
  const { node } = await mount(), card = node.querySelector('[data-queue-batch]')
  expect(card?.textContent).toContain('1 / 2 takes saved')
  expect(card?.textContent).toContain('ACE-Step 1.5')
  expect(card?.textContent).toContain('Previous attempt')
  expect(card?.textContent).toContain('Failed')
  expect([...node.querySelectorAll('button')].some(control => control.textContent?.trim() === 'Retry generation')).toBe(false)
  expect(button(node, 'Start queue').disabled).toBe(false)
})

it('aborts observation on teardown and ignores its late response without cancelling server work', async () => {
  let finish: ((value: MusicQueueSnapshot) => void) | undefined
  api.getMusicQueue.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  await mount()
  const signal: unknown = api.getMusicQueue.mock.calls[0]?.[0]
  app?.unmount(); app = undefined
  expect(signal instanceof AbortSignal && signal.aborted).toBe(true)
  finish?.(snapshot([batch()], 3, 'running')); await settle(); await vi.advanceTimersByTimeAsync(10000)
  expect(api.getMusicQueue).toHaveBeenCalledTimes(1); expect(api.controlMusicQueue).not.toHaveBeenCalled()
})

it('shows selected-configuration readiness from the preview and prevents enqueue', async () => {
  const plan = preview(), model = plan.models[0]
  if (!model) throw new Error('Missing fixture plan')
  api.previewMusicQueue.mockResolvedValue({ ...plan, can_enqueue: false, models: [{ ...model, readiness: { ...model.readiness, ready: false, weights_ready: false, error_code: 'music_queue_weights_missing' } }] })
  const { node } = await mount(); await compose(node)
  expect(node.querySelector('aside')?.textContent).toContain('Model weights are missing')
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(true)
})

it('exposes exact adapted inputs in a keyboard-owned dialog and restores focus on Escape', async () => {
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => preview(request))
  const { node } = await mount(); await compose(node)
  const opener = button(node, 'See how your brief is adapted'); opener.focus(); opener.click(); await settle()
  const dialog = document.querySelector('[role="dialog"]')
  expect(dialog?.contains(document.activeElement)).toBe(true)
  expect(dialog?.textContent).toContain('Warm piano')
  document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull(); expect(document.activeElement).toBe(opener)
})

it('clears a pending enqueue on teardown without restarting observation or cancelling server work', async () => {
  let finish: ((value: MusicQueueSnapshot) => void) | undefined
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => preview(request))
  api.enqueueMusicQueue.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node } = await mount(); await compose(node); button(node, 'Add 1 variation to queue').click(); await settle()
  const signal: unknown = api.enqueueMusicQueue.mock.calls[0]?.[1]
  app?.unmount(); app = undefined
  expect(signal instanceof AbortSignal && signal.aborted).toBe(true)
  finish?.(snapshot([batch()], 3)); await settle(); await vi.advanceTimersByTimeAsync(10000)
  expect(api.getMusicQueue).toHaveBeenCalledTimes(1); expect(api.controlMusicQueue).not.toHaveBeenCalled()
})

it('uses exact musical direction fields and displays a server duration adjustment before enqueue', async () => {
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => {
    const plan = preview(request), model = plan.models[0]
    if (!model) throw new Error('Missing fixture plan')
    return { ...plan, models: [{ ...model, effective_duration_seconds: 240, adjustments: [{ field: 'duration_seconds', code: 'music_queue_duration_clamped', requested: '300', effective: '240' }] }] }
  })
  const { node } = await mount(); await compose(node)
  await input(node, 'duration_seconds', '300'); await input(node, 'bpm', '99.5'); await input(node, 'key', 'A minor'); await input(node, 'language', 'Spanish')
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenLastCalledWith(expect.objectContaining({ brief: expect.objectContaining({ duration_seconds: 300, bpm: 99.5, key: 'A minor', language: 'Spanish' }) }), expect.any(AbortSignal))
  expect(node.querySelector('aside')?.textContent).toContain('300 → 240 seconds')
})

it.each([
  ['small-music', 120, '2:00 (120s)'],
  ['medium', 380, '6:20 (380s)'],
] as const)('displays the server duration limit for Stable %s', async (variant, stableDuration, formattedDuration) => {
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest): Promise<MusicQueuePreview> => {
    const plan = preview(request), model = plan.models[0]
    if (!model) throw new Error('Missing Stable fixture')
    return { ...plan, models: [{ ...model, effective_duration_seconds: stableDuration, adjustments: [{ field: 'duration_seconds', code: 'music_queue_duration_clamped', requested: '600', effective: String(stableDuration) }] }] }
  })
  const { node } = await mount()
  button(node, 'Instrumental').click(); await settle()
  await input(node, 'description', 'Warm piano')
  await input(node, 'duration_seconds', '600')
  const duration = node.querySelector<HTMLInputElement>('[name="duration_seconds"]')
  expect(duration?.max).toBe('600')
  expect(duration?.checkValidity()).toBe(true)
  await select(node, 'stable_audio3')
  await input(node, 'variant', variant)
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ brief: expect.objectContaining({ duration_seconds: 600 }), selections: [expect.objectContaining({ engine: 'stable_audio3', variant })] }), expect.any(AbortSignal))
  const plan = node.querySelector('aside ol li')
  expect(plan?.textContent).toContain(`Target duration: ${formattedDuration}`)
  expect(plan?.textContent).toContain(`600 → ${stableDuration} seconds`)
  expect(node.querySelector('[name="settings_mode"]')).toBeNull()
  expect(api.controlMusicQueue).not.toHaveBeenCalled(); expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
})

it('clears a Stable adapter when its target variant changes and blocks invalid strength', async () => {
  vi.mocked(adapters.listMusicAdapters).mockResolvedValue({ adapters: [{ id: 'e'.repeat(32), engine: 'stable_audio3', target: 'small-music', name: 'Piano style', sha256: 'f'.repeat(64), created_at: 'now', source_job_id: null }] })
  const { node } = await mount(); button(node, 'Instrumental').click(); await settle()
  await input(node, 'description', 'Instrumental piano'); await select(node, 'stable_audio3')
  await input(node, 'adapter-small-music', 'e'.repeat(32))
  const strength = node.querySelector('select[name="adapter-small-music"]')?.parentElement?.parentElement?.querySelector('input')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing adapter strength')
  strength.value = '3'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).not.toHaveBeenCalled()
  strength.value = '0.5'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenLastCalledWith(expect.objectContaining({ selections: [expect.objectContaining({ adapter: { id: 'e'.repeat(32), strength: 0.5 } })] }), expect.any(AbortSignal))
  await input(node, 'variant', 'medium'); await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenLastCalledWith(expect.objectContaining({ selections: [expect.objectContaining({ variant: 'medium', adapter: null })] }), expect.any(AbortSignal))
})

it('keeps completed briefs in paginated Results and explains retained older history', async () => {
  const track: SavedTrack = { id: 42, short_id: 42, model: 'stable_audio3', title: 'Saved evening', created_at: '2026-10-14', lyrics: '', seed: 42, duration_ms: 120000, wall_ms: 5000, params: {}, filename: 'song.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null }
  const saved = Array.from({ length: 7 }, (_, index) => {
    const value = batch((index + 1).toString(16).repeat(32), 'done'), item = value.items[0]
    if (!item) throw new Error('Missing fixture item')
    return { ...value, items: [{ ...item, tracks: [{ ...track, id: 42 + index, audio_url: `/api/tracks/${42 + index}/audio` }] }] }
  })
  api.getMusicQueue.mockResolvedValue({ ...snapshot(saved, 4), history_incomplete: true })
  const { node } = await mount()
  expect(node.querySelectorAll('[data-queue-batch]')).toHaveLength(0)
  expect(node.textContent).toContain('Older queue history is retained')
  button(node, 'Results').click(); await settle()
  expect(node.querySelectorAll('audio')).toHaveLength(5)
  const retained = node.querySelector('audio')
  retained?.dispatchEvent(new Event('play')); await settle()
  const next = node.querySelector<HTMLButtonElement>('[data-pagination-next]')
  if (!next) throw new Error('Missing results pagination')
  next.click(); await settle(); expect(node.querySelectorAll('audio')).toHaveLength(3)
  expect(node.querySelector('audio[data-track="42"]')).toBe(retained)
  retained?.dispatchEvent(new Event('pause')); await settle(); expect(node.querySelectorAll('audio')).toHaveLength(2)
})


it('retains a running queue through a transient observation failure and reconnects without replaying actions', async () => {
  const active = batch('a'.repeat(32), 'running')
  api.getMusicQueue.mockResolvedValueOnce(snapshot([active], 3, 'running')).mockRejectedValueOnce(new Error('/private/token')).mockResolvedValue(snapshot([batch('a'.repeat(32), 'done')], 4, 'idle'))
  const { node } = await mount()
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(node.querySelector('[data-queue-batch]')?.textContent).toContain('Evening')
  expect(node.textContent).toContain(i18n.global.t('musicQueue.errors.observe'))
  expect(node.textContent).not.toContain('/private/token')
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(api.getMusicQueue).toHaveBeenCalledTimes(3)
  expect(node.textContent).not.toContain(i18n.global.t('musicQueue.errors.observe'))
  expect(api.controlMusicQueue).not.toHaveBeenCalled(); expect(api.enqueueMusicQueue).not.toHaveBeenCalled(); expect(api.retryMusicQueue).not.toHaveBeenCalled()
})

it('queues a reviewed retained reference and blocks an invalidated review without falling back to text only', async () => {
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => preview(request))
  const { node } = await mount(); await compose(node)
  button(node, 'Review fixture reference').click(); await settle()
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenLastCalledWith(expect.objectContaining({ selections: [expect.objectContaining({ conditioning })] }), expect.any(AbortSignal))
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(false)
  button(node, 'Invalidate reference').click(); await settle()
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(true)
  expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
  await input(node, 'description', '')
  expect(node.querySelector('[data-reference]')).not.toBeNull()
  expect(button(node, 'Review fixture reference').disabled).toBe(true)
  await input(node, 'description', 'A changed brief')
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(api.previewMusicQueue).toHaveBeenLastCalledWith(expect.objectContaining({ selections: [expect.objectContaining({ conditioning })] }), expect.any(AbortSignal))
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(true)
})

it('opens a beat handoff for review without automatically accepting storage or enqueuing', async () => {
  pendingMusicConditioning.value = { source: { kind: 'beat_render', beat_id: 'a'.repeat(32), beat_revision: 1, render_id: 'b'.repeat(32), sha256: 'c'.repeat(64) }, label: 'My authored beat' }
  api.previewMusicQueue.mockImplementation(async (request: MusicQueuePreviewRequest) => preview(request))
  const { node } = await mount()
  expect(node.querySelector<HTMLInputElement>('[name="model_stable_audio3"]')?.checked).toBe(true)
  expect(node.textContent).toContain('My authored beat')
  await input(node, 'description', 'Warm piano')
  await vi.advanceTimersByTimeAsync(350); await settle()
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(true)
  expect(api.enqueueMusicQueue).not.toHaveBeenCalled(); expect(api.controlMusicQueue).not.toHaveBeenCalled()
})

it('opens the source task setup even when ordinary ACE weights are missing without enqueuing a text fallback', async () => {
  api.musicQueueCatalog.mockResolvedValue({ ...catalog, engines: catalog.engines.map(row => row.engine === 'ace_step' ? { ...row, ready: false, weights_ready: false } : row) })
  const { node } = await mount()
  await input(node, 'description', 'Add warm strings')
  button(node, 'Use audio with ACE-Step').click(); await settle()
  expect(node.querySelector<HTMLSelectElement>('[name="ace-song-mode"]')?.value).toBe('ace_complete')
  expect(node.textContent).toContain('Prepare ACE-Step Base')
  expect(node.querySelector('a[href="/settings#module-ace_step"]')?.textContent).toBeTruthy()
  expect(node.textContent).toContain('Open automatic ACE-Step runtime setup or repair')
  expect(button(node, 'Add 1 variation to queue').disabled).toBe(true)
  expect(api.enqueueMusicQueue).not.toHaveBeenCalled()
})
