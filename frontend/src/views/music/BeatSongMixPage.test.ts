// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import * as api from '../../api/beatSongMixes'
import * as versions from '../../api/audioVersions'
import * as beats from '../../api/beats'
import previewFixture from '../../api/beatSongMixPreviewFixture.json'
import jobFixture from '../../api/beatSongMixJobFixture.json'
import { parseBeatSongMixJob, parseBeatSongMixPreview } from '../../api/generated'
import { beatSongMixesEnglish } from '../../locales/beatSongMixes'
import BeatSongMixPage from './BeatSongMixPage.vue'
import { beatDocument, beatJob } from './beatTestFixtures'

vi.mock('../../api/beatSongMixes', async original => ({ ...await original<typeof import('../../api/beatSongMixes')>(), getBeatSongPlan: vi.fn(), listBeatSongJobs: vi.fn(), getBeatSongJob: vi.fn(), inspectBeatSongSource: vi.fn(), createBeatSongPlan: vi.fn(), updateBeatSongPlan: vi.fn(), startBeatSongMix: vi.fn(), retryBeatSongSave: vi.fn(), cancelBeatSongMix: vi.fn(), publishBeatSongProject: vi.fn() }))
vi.mock('../../components/shared/TrackAudioVersions.vue', () => ({ default: { props: ['trackId'], template: '<div data-published-track :data-track-id="trackId"><audio preload="none" /></div>' } }))
vi.mock('../../api/audioVersions', () => ({ listAudioVersions: vi.fn() }))
vi.mock('../../api/beats', () => ({ listBeats: vi.fn(), listBeatJobs: vi.fn() }))
vi.mock('../../components/editor/LibraryPicker.vue', () => ({ default: { emits: ['pick', 'close'], template: '<div><button @click="$emit(\'pick\', {trackId:1,sourceLabel:\'Track A\'})">Track A</button><button @click="$emit(\'pick\', {trackId:2,sourceLabel:\'Track B\'})">Track B</button></div>' } }))
const preview = () => parseBeatSongMixPreview(structuredClone(previewFixture))
const job = () => parseBeatSongMixJob(structuredClone(jobFixture))
let app: App | undefined
async function settle() { for (let i = 0; i < 20; i++) await nextTick() }
function button(node: ParentNode, label: string): HTMLButtonElement { const value = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!value) throw new Error(`Missing ${label}`); return value }
async function mount(planId?: string) {
  const node = document.body.appendChild(document.createElement('div')); app = createApp(BeatSongMixPage, { planId }).use(i18n)
  app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot/></a>' }); app.mount(node); await settle(); return node
}
async function check(node: ParentNode, key: string) { const input = node.querySelector<HTMLInputElement>(`[data-${key}]`); if (!input) throw new Error(`Missing ${key}`); input.click(); await settle() }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); localStorage.clear(); i18n.global.mergeLocaleMessage('en', { beatSongMixes: beatSongMixesEnglish })
  vi.mocked(api.getBeatSongPlan).mockResolvedValue(preview().plan); vi.mocked(api.listBeatSongJobs).mockResolvedValue({ jobs: [] })
  vi.mocked(api.updateBeatSongPlan).mockImplementation(async (_id, body) => ({ ...preview(), plan: { ...preview().plan, revision: body.expected_revision + 1, draft: body.draft } }))
  vi.mocked(api.createBeatSongPlan).mockResolvedValue(preview())
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })

it('reloads a retained plan without rendering and requires fresh CAS/storage confirmation', async () => {
  const node = await mount(preview().plan.id)
  expect(api.inspectBeatSongSource).not.toHaveBeenCalled(); expect(api.startBeatSongMix).not.toHaveBeenCalled()
  expect(button(node, 'Render full mix').disabled).toBe(true)
  await check(node, 'timing-confirm'); button(node, 'Review plan and disk space').click(); await settle()
  expect(api.updateBeatSongPlan).toHaveBeenCalledWith(preview().plan.id, expect.objectContaining({ expected_revision: 0 }), expect.any(AbortSignal))
  await check(node, 'storage-confirm'); expect(button(node, 'Render full mix').disabled).toBe(false)
  const bpm = node.querySelector<HTMLInputElement>('[data-bpm]'); if (!bpm) throw new Error('Missing BPM')
  bpm.value = '125'; bpm.dispatchEvent(new Event('input')); await settle()
  expect(button(node, 'Render full mix').disabled).toBe(true); expect(node.querySelector('[data-storage-confirm]')).toBeNull()
})
it('offers save-only recovery and existing editor/audio outputs without another render', async () => {
  const saving = { ...job(), status: 'saving' as const, error: { code: 'song_save_failed' as const } }
  vi.mocked(api.listBeatSongJobs).mockResolvedValue({ jobs: [saving, { ...job(), id: 'e'.repeat(32) }] })
  vi.mocked(api.retryBeatSongSave).mockResolvedValue(job())
  const node = await mount(); button(node, 'Retry saving completed audio').click(); await settle()
  expect(api.retryBeatSongSave).toHaveBeenCalledWith(saving.id, expect.any(AbortSignal)); expect(api.startBeatSongMix).not.toHaveBeenCalled()
  expect(node.querySelector('a[href="/editor/1"]')).not.toBeNull(); expect(node.querySelector('audio')?.getAttribute('preload')).toBe('none')
  expect(node.querySelector('[data-published-track]')).toBeNull()
  const tools = [...node.querySelectorAll('details')].find(value => value.querySelector('summary')?.textContent === 'Saved versions, analysis and exports')
  if (!tools) throw new Error('Missing saved track tools')
  tools.open = true; tools.dispatchEvent(new Event('toggle')); await settle()
  expect(node.querySelector('[data-published-track][data-track-id="3"]')).not.toBeNull()
  app?.unmount(); app = undefined; expect(api.cancelBeatSongMix).not.toHaveBeenCalled()
})
it('does not apply plan A after a later plan B was opened', async () => {
  let resolveA: ((value: ReturnType<typeof preview>['plan']) => void) | undefined
  vi.mocked(api.getBeatSongPlan).mockImplementationOnce(() => new Promise(resolve => { resolveA = resolve })).mockResolvedValueOnce({ ...preview().plan, id: 'b'.repeat(32), draft: { ...preview().plan.draft, title: 'Plan B' } })
  const node = await mount('a'.repeat(32)), field = node.querySelector<HTMLInputElement>('[data-plan-id]'); if (!field) throw new Error('Missing plan lookup')
  field.value = 'b'.repeat(32); field.dispatchEvent(new Event('input')); button(node, 'Open saved plan').click(); await settle()
  resolveA?.({ ...preview().plan, id: 'a'.repeat(32), draft: { ...preview().plan.draft, title: 'Stale A' } }); await settle()
  expect(node.querySelector<HTMLInputElement>('[data-title]')?.value).toBe('Plan B'); expect(api.startBeatSongMix).not.toHaveBeenCalled()
})
it('rejects late inspection when the selected library version changes', async () => {
  let resolveA: ((value: ReturnType<typeof preview>['plan']['beat']) => void) | undefined
  vi.mocked(versions.listAudioVersions).mockImplementation(async trackId => ({ track_id: trackId, original_available: true, versions: [{ id: trackId === 1 ? preview().plan.beat.version_id : preview().plan.song.version_id, track_id: trackId, kind: 'original', status: 'done', created_at: '2026-10-17T12:00:00Z', audio_url: `/api/tracks/${trackId}/audio` }] }))
  vi.mocked(api.inspectBeatSongSource).mockImplementationOnce(() => new Promise(resolve => { resolveA = resolve }))
  const node = await mount(); button(node, 'Choose beat audio').click(); await settle(); button(document.body, 'Track A').click(); await settle()
  button(node, 'Inspect beat source').click(); await settle(); button(node, 'Choose beat audio').click(); await settle(); button(document.body, 'Track B').click(); await settle()
  resolveA?.(preview().plan.beat); await settle()
  expect(node.querySelector('[data-source-beat]')?.textContent).not.toContain(preview().plan.beat.sha256)
  expect(button(node, 'Review plan and disk space').disabled).toBe(true)
})

it('keeps an ambiguous exact submission across reload and only recovers after an explicit action', async () => {
  const result = job(); localStorage.setItem('openfabric.beat-song.pending', JSON.stringify(result.request))
  vi.mocked(api.startBeatSongMix).mockResolvedValue(result)
  const node = await mount(); expect(api.startBeatSongMix).not.toHaveBeenCalled(); expect(button(node, 'Render full mix').disabled).toBe(true)
  button(node, 'Recover submission').click(); await settle()
  expect(api.startBeatSongMix).toHaveBeenCalledWith(result.request, expect.any(AbortSignal))
  expect(localStorage.getItem('openfabric.beat-song.pending')).toBeNull(); expect(node.querySelector('a[href="/editor/1"]')).not.toBeNull()
})
it('recognizes a proven saved enqueue from history despite server-added optional defaults', async () => {
  const result = job(), request = { plan_id: result.plan.id, expected_revision: result.plan.revision, plan_sha256: result.plan.sha256, submission_key: result.request.submission_key, mode: 'full' }
  localStorage.setItem('openfabric.beat-song.pending', JSON.stringify(request)); vi.mocked(api.listBeatSongJobs).mockResolvedValue({ jobs: [result] })
  const node = await mount()
  expect(localStorage.getItem('openfabric.beat-song.pending')).toBeNull(); expect(node.textContent).not.toContain('Recover submission'); expect(api.startBeatSongMix).not.toHaveBeenCalled()
})

it('requires timing review again after BPM or anchor edits', async () => {
  const node = await mount(preview().plan.id); await check(node, 'timing-confirm')
  const bpm = node.querySelector<HTMLInputElement>('[data-bpm]'); if (!bpm) throw new Error('Missing BPM')
  bpm.value = '100'; bpm.dispatchEvent(new Event('input')); await settle()
  expect(node.querySelector<HTMLInputElement>('[data-timing-confirm]')?.checked).toBe(false)
  expect(button(node, 'Review plan and disk space').disabled).toBe(true)
})

it('does not render when disk fits but the required native runtime is unavailable', async () => {
  vi.mocked(api.updateBeatSongPlan).mockImplementation(async (_id, body) => ({ ...preview(), plan: { ...preview().plan, revision: 1, draft: body.draft, needs_native: true, native_ready: false } }))
  const node = await mount(preview().plan.id); await check(node, 'timing-confirm'); button(node, 'Review plan and disk space').click(); await settle(); await check(node, 'storage-confirm')
  expect(button(node, 'Render full mix').disabled).toBe(true); expect(node.textContent).toContain('verified Audio DSP runtime')
})

it('submits the reviewed revision/hash and revokes preview approval when its range changes', async () => {
  vi.mocked(api.startBeatSongMix).mockImplementation(async request => ({ ...job(), plan: { ...preview().plan, revision: request.expected_revision }, request, manifest: null, status: 'queued' }))
  const node = await mount(preview().plan.id); await check(node, 'timing-confirm'); button(node, 'Review plan and disk space').click(); await settle(); await check(node, 'storage-confirm')
  const end = [...node.querySelectorAll<HTMLInputElement>('input')].find(input => input.parentElement?.textContent?.trim().startsWith('Preview end'))
  if (!end) throw new Error('Missing preview range')
  end.value = '0.1'; end.dispatchEvent(new Event('input')); await settle()
  expect(button(node, 'Render preview').disabled).toBe(true)
  await check(node, 'storage-confirm'); button(node, 'Render preview').click(); await settle()
  expect(api.startBeatSongMix).toHaveBeenCalledWith(expect.objectContaining({ plan_id: preview().plan.id, expected_revision: 1, plan_sha256: preview().plan.sha256, mode: 'preview', preview_start_sec: 0, preview_end_sec: .1 }), expect.any(AbortSignal))
})

it('completes explicit two-source inspection, anchors, review and enqueue without automatic work', async () => {
  vi.mocked(versions.listAudioVersions).mockImplementation(async trackId => ({ track_id: trackId, original_available: true, versions: [{ id: trackId === 1 ? preview().plan.beat.version_id : preview().plan.song.version_id, track_id: trackId, kind: 'original', status: 'done', created_at: '2026-10-17T12:00:00Z' }] }))
  vi.mocked(api.inspectBeatSongSource).mockImplementation(async body => body.source.kind === 'version' && body.source.track_id === 1 ? preview().plan.beat : preview().plan.song)
  vi.mocked(api.startBeatSongMix).mockImplementation(async request => ({ ...job(), request }))
  const node = await mount(), title = node.querySelector<HTMLInputElement>('[data-title]'); if (!title) throw new Error('Missing title')
  title.value = 'Owned layer'; title.dispatchEvent(new Event('input'))
  button(node, 'Choose beat audio').click(); await settle(); button(document.body, 'Track A').click(); await settle(); button(node, 'Inspect beat source').click(); await settle()
  button(node, 'Choose song audio').click(); await settle(); button(document.body, 'Track B').click(); await settle(); button(node, 'Inspect song source').click(); await settle()
  expect(api.createBeatSongPlan).not.toHaveBeenCalled(); expect(api.startBeatSongMix).not.toHaveBeenCalled()
  button(node, 'Use excerpt endpoints').click(); await settle(); await check(node, 'timing-confirm'); button(node, 'Review plan and disk space').click(); await settle()
  expect(api.createBeatSongPlan).toHaveBeenCalledWith(expect.objectContaining({ anchors: [{ source_sec: 0, target_sec: 0 }, { source_sec: .2, target_sec: .2 }], beat: expect.objectContaining({ sha256: preview().plan.beat.sha256 }), song: expect.objectContaining({ sha256: preview().plan.song.sha256 }) }), expect.any(AbortSignal))
  expect(api.startBeatSongMix).not.toHaveBeenCalled(); await check(node, 'storage-confirm'); button(node, 'Render full mix').click(); await settle()
  expect(api.startBeatSongMix).toHaveBeenCalledOnce(); expect(localStorage.getItem('openfabric.beat-song.pending')).toBeNull()
})

it('uses an authored beat musical span rather than its audio tail in endpoint anchors', async () => {
  const doc = beatDocument(), full = beatJob({ status: 'done', manifest: { schema_version: 1, render_id: 'e'.repeat(32), beat_id: doc.id, beat_revision: doc.revision, seed: doc.seed, renderer_version: 'test-v1', algorithm_version: 'test-v1', peak: .1, musical_frames: 96000, document_sha256: 'a'.repeat(64), events_sha256: 'b'.repeat(64), kit_manifest_sha256: doc.kit_manifest_sha256, sample_rate_hz: 48000, channels: 2, frames: 120000, artifacts: [{ id: 'c'.repeat(32), kind: 'mix', filename: 'mix.wav', sha256: preview().plan.beat.sha256, byte_size: 1000, media_type: 'audio/wav' }], warnings: [], track_id: 1 } })
  vi.mocked(beats.listBeats).mockResolvedValue({ documents: [{ id: doc.id, revision: 0, title: doc.title, bpm: 120, updated_at: '2026-10-17T12:00:00Z' }] }); vi.mocked(beats.listBeatJobs).mockResolvedValue({ jobs: [full] })
  vi.mocked(api.inspectBeatSongSource).mockImplementation(async body => ({ ...preview().plan.beat, source: body.source, beat_document: doc, duration_sec: 2.5, musical_duration_sec: 2, frames: 120000 }))
  const node = await mount(preview().plan.id); button(node, 'Load saved beats').click(); await settle()
  const select = [...node.querySelectorAll('select')].find(item => item.parentElement?.textContent?.trim().startsWith('Beat document')); if (!select) throw new Error('Missing beat document')
  select.value = doc.id; select.dispatchEvent(new Event('change')); await settle(); button(node, 'Load full renders').click(); await settle()
  const render = [...node.querySelectorAll('select')].find(item => item.parentElement?.textContent?.trim().startsWith('Completed full render')); if (!render) throw new Error('Missing render')
  render.value = full.id; render.dispatchEvent(new Event('change')); await settle(); button(node, 'Select this render').click(); await settle(); button(node, 'Inspect beat source').click(); await settle(); button(node, 'Use excerpt endpoints').click(); await settle()
  const moving = [...node.querySelectorAll<HTMLInputElement>('input')].filter(input => input.parentElement?.textContent?.trim() === 'Moving source seconds')
  expect(moving.at(-1)?.value).toBe('2'); expect(api.inspectBeatSongSource).toHaveBeenCalledWith({ source: { kind: 'beat_render', beat_id: doc.id, beat_revision: 0, render_id: full.id, sha256: preview().plan.beat.sha256 } }, expect.any(AbortSignal))
})

it('suspends history polling while a cancel action is awaiting its owned result', async () => {
  const running = { ...job(), status: 'running' as const, manifest: null }
  let finish: ((value: ReturnType<typeof job>) => void) | undefined
  vi.mocked(api.listBeatSongJobs).mockResolvedValue({ jobs: [running] }); vi.mocked(api.cancelBeatSongMix).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const node = await mount(); button(node, 'Cancel render').click(); await settle(); await vi.advanceTimersByTimeAsync(1600); await settle()
  expect(api.listBeatSongJobs).toHaveBeenCalledOnce()
  finish?.({ ...running, status: 'cancelled' }); await settle(); expect(node.textContent).toContain('Cancelled')
})
