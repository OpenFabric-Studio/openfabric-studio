// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n, setLocale } from '../../i18n'
import { ApiError } from '../../api/http'
import * as api from '../../api/beats'
import type { BeatDocument } from '../../api/generated'
import { beatDocument, beatJob, beatKit } from './beatTestFixtures'
import BeatMakerPage from './BeatMakerPage.vue'
import { pendingAceSongSource, pendingMusicConditioning } from '../../composables/generationDrafts'

vi.mock('../../api/beats', async original => ({ ...await original<typeof import('../../api/beats')>(), listBeats: vi.fn(), listBeatKits: vi.fn(), getBeat: vi.fn(), getBeatRevision: vi.fn(), publishBeatProject: vi.fn(), listBeatJobs: vi.fn(), listBeatRevisions: vi.fn(), createBeat: vi.fn(), saveBeat: vi.fn(), varyBeat: vi.fn(), humanizeBeat: vi.fn(), restoreBeat: vi.fn(), previewBeatStorage: vi.fn(), renderBeat: vi.fn(), cancelBeatJob: vi.fn(), retryBeatSave: vi.fn(), importBeatKit: vi.fn() }))
let app: App | undefined
beforeEach(() => {
  pendingAceSongSource.value = null; pendingMusicConditioning.value = null
  vi.clearAllMocks(); vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-17T12:00:20Z')); localStorage.clear(); setLocale('en')
  const doc = beatDocument()
  vi.mocked(api.listBeats).mockResolvedValue({ documents: [{ id: doc.id, revision: doc.revision, title: doc.title, bpm: doc.bpm, updated_at: '2026-10-17T12:00:00Z' }] })
  vi.mocked(api.listBeatKits).mockResolvedValue({ kits: [beatKit()] })
  vi.mocked(api.getBeat).mockResolvedValue(doc)
  vi.mocked(api.listBeatJobs).mockResolvedValue({ jobs: [] })
  vi.mocked(api.listBeatRevisions).mockResolvedValue({ revisions: [{ revision: 0, document_sha256: 'c'.repeat(64), title: doc.title, created_at: '2026-10-17T12:00:00Z' }] })
  vi.mocked(api.saveBeat).mockImplementation(async (_id, request) => ({ ...request.document, revision: request.expected_revision + 1 }))
  vi.mocked(api.previewBeatStorage).mockResolvedValue({ output_bytes: 1000, temporary_bytes: 2000, required_free_bytes: 3000, free_bytes: 4000, can_start: true, frames: 96000, duration_sec: 2 })
  vi.mocked(api.renderBeat).mockImplementation(async (_id, request) => beatJob({ request }))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let i = 0; i < 12; i++) await nextTick() }
async function mount(props: { beatId?: string; revision?: string } = {}) { const node = document.body.appendChild(document.createElement('div')); app = createApp(BeatMakerPage, props).use(i18n); app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' }); app.mount(node); await settle(); return node }
function button(node: HTMLElement, text: string) { const result = [...node.querySelectorAll('button')].find(value => value.textContent?.trim() === text); if (!result) throw new Error(`Missing ${text}`); return result }
function step(node: HTMLElement) { const result = node.querySelector<HTMLButtonElement>('[aria-label="Drum 1, step 1"]'); if (!result) throw new Error('Missing step'); return result }

it('loads a saved beat and grid without rendering or starting a model', async () => {
  const node = await mount()
  expect(node.querySelector('h2')?.textContent).toBe('Beat Maker'); expect(node.querySelectorAll('table button')).toHaveLength(128)
  expect(step(node).getAttribute('aria-pressed')).toBe('false')
  expect(api.previewBeatStorage).not.toHaveBeenCalled(); expect(api.renderBeat).not.toHaveBeenCalled()
})
it('opens a pinned editor revision without overwriting the current saved beat', async () => {
  const current = { ...beatDocument(), revision: 3, title: 'Current arrangement' }
  vi.mocked(api.getBeat).mockResolvedValue(current)
  vi.mocked(api.getBeatRevision).mockResolvedValue({ ...beatDocument(), title: 'Pinned arrangement' })
  const node = await mount({ beatId: current.id, revision: '0' })
  expect(api.getBeatRevision).toHaveBeenCalledWith(current.id, 0, expect.any(AbortSignal))
  expect(node.querySelector<HTMLInputElement>('input[maxlength="120"]')?.value).toBe('Current arrangement')
  expect(api.saveBeat).not.toHaveBeenCalled()
  button(node, 'Use pinned revision as a new draft').click(); await settle()
  expect(node.querySelector<HTMLInputElement>('input[maxlength="120"]')?.value).toBe('Pinned arrangement')
  button(node, 'Save beat').click(); await settle()
  expect(api.saveBeat).toHaveBeenCalledWith(current.id, expect.objectContaining({ expected_revision: 3, document: expect.objectContaining({ revision: 3, title: 'Pinned arrangement' }) }), expect.any(AbortSignal))
})
it('prepares an editor project only from a completed full render and does not show a finished ETA', async () => {
  const job = beatJob({ status: 'done', finished_at: '2026-10-17T12:00:10Z' })
  vi.mocked(api.listBeatJobs).mockResolvedValue({ jobs: [job] })
  vi.mocked(api.publishBeatProject).mockResolvedValue({ project_id: 17, provenance: { workflow: 'beat', workflow_id: job.beat_id, workflow_revision: job.beat_revision, source_manifest_sha256: 'f'.repeat(64) } })
  const node = await mount()
  expect(node.textContent).not.toContain('ETA available after measured progress')
  button(node, 'Prepare editor project').click(); await settle()
  expect(api.publishBeatProject).toHaveBeenCalledWith(job.id, job.beat_id, job.beat_revision, expect.any(AbortSignal))
  expect(node.querySelector('a[href="/editor/17"]')?.textContent).toBe('Open in editor')
  expect(api.renderBeat).not.toHaveBeenCalled()
})
it('edits a step, retains a browser draft and saves a revision before rendering', async () => {
  const node = await mount(); step(node).click(); await settle()
  expect(step(node).getAttribute('aria-pressed')).toBe('true'); expect(node.textContent).toContain('Unsaved changes')
  expect(button(node, 'Prepare full beat').disabled).toBe(true)
  expect(localStorage.getItem(`openfabric-beat-draft:${beatDocument().id}`)).toContain('"index":0')
  button(node, 'Save beat').click(); await settle()
  expect(api.saveBeat).toHaveBeenCalledWith(beatDocument().id, expect.objectContaining({ expected_revision: 0, document: expect.objectContaining({ revision: 0 }) }), expect.any(AbortSignal))
  expect(node.textContent).toContain('Saved revision 1'); expect(button(node, 'Prepare full beat').disabled).toBe(false)
})
it('recovers a validated local draft before replacing browser storage', async () => {
  const doc = beatDocument(); doc.title = 'Recovered idea'
  localStorage.setItem(`openfabric-beat-draft:${doc.id}`, JSON.stringify(doc))
  const node = await mount()
  expect(node.textContent).toContain('Recovered your unsaved local draft')
  expect(node.querySelector<HTMLInputElement>('input[maxlength="120"]')?.value).toBe('Recovered idea')
  expect(button(node, 'Save beat').disabled).toBe(false); expect(api.saveBeat).not.toHaveBeenCalled()
})
it('preserves a conflicted draft and never renders unsaved changes', async () => {
  vi.mocked(api.saveBeat).mockRejectedValueOnce(new ApiError('beat_revision_conflict', 409))
  const node = await mount(); step(node).click(); await settle(); button(node, 'Save beat').click(); await settle()
  expect(node.textContent).toContain('changed in another window'); expect(step(node).getAttribute('aria-pressed')).toBe('true')
  expect(button(node, 'Prepare full beat').disabled).toBe(true); expect(api.renderBeat).not.toHaveBeenCalled()
})
it('requires explicit space review and a second action to submit the exact revision', async () => {
  const node = await mount(); button(node, 'Prepare full beat').click(); await settle()
  expect(api.previewBeatStorage).toHaveBeenCalledWith(beatDocument().id, expect.objectContaining({ expected_revision: 0, mode: 'full' }), expect.any(AbortSignal))
  expect(api.renderBeat).not.toHaveBeenCalled(); button(node, 'Render reviewed beat').click(); await settle()
  const reviewed = vi.mocked(api.previewBeatStorage).mock.calls[0]?.[1]
  expect(api.renderBeat).toHaveBeenCalledWith(beatDocument().id, reviewed, expect.any(AbortSignal))
  expect(node.textContent).toContain('Rendering continues when you leave this page')
})
it('invalidates space review when MIDI settings change', async () => {
  const node = await mount(); button(node, 'Prepare full beat').click(); await settle()
  const checkbox = [...node.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find(input => input.parentElement?.textContent?.includes('Approximate MIDI'))
  checkbox?.click(); await settle()
  expect([...node.querySelectorAll('button')].some(item => item.textContent === 'Render reviewed beat')).toBe(false)
  expect(api.renderBeat).not.toHaveBeenCalled()
})
it('reattaches and cancels an existing render while displaying measured time and ETA', async () => {
  vi.mocked(api.listBeatJobs).mockResolvedValue({ jobs: [beatJob()] })
  vi.mocked(api.cancelBeatJob).mockResolvedValue(beatJob({ status: 'cancelled', phase: 'cancelled' }))
  const node = await mount(); expect(node.textContent).toContain('20s elapsed'); expect(node.textContent).toContain('Rendering ETA: ~20s')
  button(node, 'Cancel render').click(); await settle(); expect(api.cancelBeatJob).toHaveBeenCalledWith(beatJob().id, expect.any(AbortSignal))
  expect(api.renderBeat).not.toHaveBeenCalled()
})
it('aborts a late document load on teardown and never starts background polling afterwards', async () => {
  let finish: ((value: BeatDocument) => void) | undefined
  vi.mocked(api.getBeat).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const node = await mount(); expect(node.textContent).toContain('Loading saved beats')
  const signal = vi.mocked(api.getBeat).mock.calls[0]?.[1]
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  finish?.(beatDocument()); await settle(); await vi.advanceTimersByTimeAsync(5000)
  expect(api.listBeatJobs).toHaveBeenCalledTimes(1); expect(api.renderBeat).not.toHaveBeenCalled()
})

it('retains hits outside a smaller grid until the user explicitly removes them', async () => {
  const doc = beatDocument(), pattern = doc.patterns[0], lane = pattern?.lanes[0]
  if (!pattern || !lane) throw new Error('Missing fixture pattern')
  pattern.bars = 2; lane.steps = [{ index: 20 }]
  vi.mocked(api.getBeat).mockResolvedValue(doc)
  const node = await mount()
  const select = [...node.querySelectorAll<HTMLSelectElement>('select')].find(input => input.parentElement?.textContent?.trim().startsWith('Bars'))
  if (!select) throw new Error('Missing bar selector')
  select.value = '1'; select.dispatchEvent(new Event('change')); await settle()
  expect(localStorage.getItem(`openfabric-beat-draft:${doc.id}`)).toContain('"index":20')
  expect(button(node, 'Save beat').disabled).toBe(true)
  button(node, 'Remove 1 hits outside the new grid').click(); await settle()
  expect(localStorage.getItem(`openfabric-beat-draft:${doc.id}`)).not.toContain('"index":20')
  expect(button(node, 'Save beat').disabled).toBe(false)
})

it('hands a completed full render to the ACE source queue without approving storage or starting work', async () => {
  const doc = beatDocument(), render = beatJob()
  const job = beatJob({ status: 'done', manifest: { schema_version: 1, render_id: render.id, beat_id: doc.id, beat_revision: doc.revision, seed: doc.seed, renderer_version: 'test-v1', algorithm_version: 'test-v1', peak: .1, musical_frames: 96000, document_sha256: 'a'.repeat(64), events_sha256: 'b'.repeat(64), kit_manifest_sha256: doc.kit_manifest_sha256, sample_rate_hz: 48000, channels: 2, frames: 103200, artifacts: [{ id: 'c'.repeat(32), kind: 'mix', filename: 'mix.wav', sha256: 'd'.repeat(64), byte_size: 1000, media_type: 'audio/wav' }], warnings: [], track_id: 1 } })
  vi.mocked(api.listBeatJobs).mockResolvedValue({ jobs: [job] })
  const node = await mount(), link = [...node.querySelectorAll('a')].find(value => value.textContent === 'Build a song from audio')
  if (!link) throw new Error('Missing song handoff')
  link.click(); await settle()
  expect(link.getAttribute('href')).toBe('/music')
  expect(pendingAceSongSource.value?.source).toEqual({ kind: 'beat_render', beat_id: doc.id, beat_revision: doc.revision, render_id: job.id, sha256: 'd'.repeat(64) })
  expect(pendingMusicConditioning.value).toBeNull()
  expect(api.renderBeat).not.toHaveBeenCalled(); expect(api.previewBeatStorage).not.toHaveBeenCalled()
})
