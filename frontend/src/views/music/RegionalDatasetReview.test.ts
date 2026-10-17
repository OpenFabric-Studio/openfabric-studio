// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import * as api from '../../api/regionalDatasets'
import * as training from '../../api/stableLora'
import { ApiError } from '../../api/http'
import type { RegionalComparisonState, RegionalDatasetReview as RegionalReview, RegionalModelCard, RegionalPreparationJob, RegionalRecordingInspection } from '../../api/generated'
import RegionalDatasetReview from './RegionalDatasetReview.vue'
import { acousticJobFixture, acousticReviewFixture } from './regionalAcousticTestFixtures'

const fixtures = vi.hoisted(() => {
  const make = (id: string, name: string) => ({ dataset: { id: id.repeat(32), name, dataset_path: 'datasets/fixture/dataset.json',
    audio_dir: 'datasets/fixture', sample_count: 1, labeled_count: 1, revision: 1, available: true, trashed: false },
  samples: [{ id: 'phrase', filename: `${name}.wav`, caption: 'Reviewed phrase', labeled: true }], metadata: {} })
  return { a: make('a', 'Dataset A'), b: make('b', 'Dataset B') }
})
vi.mock('./TrainingDatasetPicker.vue', () => ({ default: { template: '<div><button @click="$emit(\'pick\', a)">Dataset A</button><button @click="$emit(\'pick\', b)">Dataset B</button></div>', setup: () => fixtures } }))
vi.mock('./MusicTrainingSetup.vue', () => ({ default: { template: '<span />' } }))
vi.mock('../../components/shared/MusicTermsCard.vue', () => ({ default: { template: '<span />' } }))
vi.mock('../../api/regionalDatasets', () => ({ inspectRegionalRecording: vi.fn(), loadRegionalReview: vi.fn(), listRegionalPreparations: vi.fn(),
  getRegionalPreparation: vi.fn(), saveRegionalReview: vi.fn(), previewRegionalDataset: vi.fn(), prepareRegionalDataset: vi.fn(),
  regionalTrainingSelection: vi.fn(), controlRegionalPreparation: vi.fn(), submitRegionalTraining: vi.fn(),
  listRegionalComparisons: vi.fn(), getRegionalComparison: vi.fn(), createRegionalComparison: vi.fn(),
  controlRegionalArm: vi.fn(), saveRegionalListeningReview: vi.fn(), openRegionalBlindSession: vi.fn(), saveRegionalBlindReview: vi.fn(), getRegionalModelCard: vi.fn(), regionalExcerptUrl: vi.fn(), listRegionalAcousticScans: vi.fn(), startRegionalAcousticScan: vi.fn(), getRegionalAcousticScan: vi.fn(), cancelRegionalAcousticScan: vi.fn(), regionalAcousticAuditionUrl: vi.fn() }))
vi.mock('../../api/stableLora', () => ({ stableLoraStatus: vi.fn(), listStableLora: vi.fn(), getStableLora: vi.fn(), cancelStableLora: vi.fn() }))

let app: App | undefined
async function settle() { for (let index = 0; index < 18; index++) await nextTick() }
function button(node: ParentNode, label: string): HTMLButtonElement {
  const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!found) throw new Error(`Missing ${label}`)
  return found
}
async function mount() {
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp(RegionalDatasetReview).use(i18n); app.mount(node); await settle(); return node
}
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear()
  vi.mocked(api.loadRegionalReview).mockRejectedValue(new ApiError('regional_dataset_not_found', 404))
  vi.mocked(api.listRegionalPreparations).mockResolvedValue({ jobs: [] })
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({ jobs: [] })
  vi.mocked(api.inspectRegionalRecording).mockImplementation(async body => ({ recording_id: body.recording_id, source_sha256: 'c'.repeat(64),
    decoded_pcm_sha256: 'd'.repeat(64), source_bytes: 16044, sample_rate_hz: 8000, channels: 1, frames: 8000 }))
  vi.mocked(training.stableLoraStatus).mockResolvedValue({ supported: false, installed: false, small_weights_ready: false, medium_weights_ready: false })
  vi.mocked(training.listStableLora).mockResolvedValue({ jobs: [], history_incomplete: false })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear() })

it('reuses saved dataset selection and requires explicit source inspection without launching native work', async () => {
  const node = await mount()
  button(node, 'Dataset A').click(); await settle()
  expect(api.inspectRegionalRecording).not.toHaveBeenCalled()
  button(node, 'Inspect recording').click(); await settle()
  expect(api.inspectRegionalRecording).toHaveBeenCalledWith(expect.objectContaining({ source: { dataset_id: 'a'.repeat(32), revision: 1, sample_id: 'phrase' } }), expect.any(AbortSignal))
  expect(node.textContent).toContain('c'.repeat(64))
  expect(api.prepareRegionalDataset).not.toHaveBeenCalled(); expect(api.submitRegionalTraining).not.toHaveBeenCalled()
})

it('aborts stale inspection when another dataset replaces the active review', async () => {
  let resolve: ((value: RegionalRecordingInspection) => void) | undefined
  vi.mocked(api.inspectRegionalRecording).mockImplementation(() => new Promise(value => { resolve = value }))
  const node = await mount()
  button(node, 'Dataset A').click(); await settle()
  button(node, 'Inspect recording').click(); await settle()
  const request = vi.mocked(api.inspectRegionalRecording).mock.calls[0]
  button(node, 'Dataset B').click(); await settle()
  expect(request?.[1]?.aborted).toBe(true)
  if (!request || !resolve) throw new Error('Missing pending inspection')
  resolve({ recording_id: request[0].recording_id, source_sha256: 'f'.repeat(64), decoded_pcm_sha256: 'e'.repeat(64),
    source_bytes: 16044, sample_rate_hz: 8000, channels: 1, frames: 8000 }); await settle()
  expect(node.textContent).toContain('Dataset B'); expect(node.textContent).not.toContain('f'.repeat(64))
})

it('starts read-only setup inspection when a previously disabled lazy panel becomes usable', async () => {
  const disabled = ref(true), node = document.body.appendChild(document.createElement('div'))
  app = createApp({ setup: () => () => h(RegionalDatasetReview, { disabled: disabled.value }) }).use(i18n)
  app.mount(node); await settle()
  expect(training.stableLoraStatus).not.toHaveBeenCalled()
  disabled.value = false; await settle()
  expect(training.stableLoraStatus).toHaveBeenCalledTimes(1)
  expect(api.submitRegionalTraining).not.toHaveBeenCalled()
})

it('requires storage confirmation for a saved review and invalidates it on later edits', async () => {
  const review: RegionalReview = { revision: 1, sha256: 'e'.repeat(64), created_at: '2026-10-17T00:00:00Z', request: {
    dataset_id: 'a'.repeat(32), dataset_revision: 1, expected_revision: 0, tradition: 'Fixture tradition', recordings: [{
      id: 'b'.repeat(32), source: { dataset_id: 'a'.repeat(32), revision: 1, sample_id: 'phrase' }, source_sha256: 'c'.repeat(64),
      caption: 'Reviewed phrase', captions_reviewed: true, original_recording_sha256: 'c'.repeat(64), rights: [{ id: 'd'.repeat(32),
        source_sha256: 'c'.repeat(64), evidence_sha256: 'e'.repeat(64), restriction: 'conditional', reviewed_by: 'Fixture reviewer',
        reviewed_at: '2026-10-17', private_reference: 'fixture-document', permissions: { reference: 'allowed', training: 'allowed' } }] }],
    excerpts: [{ id: 'f'.repeat(32), recording_id: 'b'.repeat(32), start_frame: 0, end_frame: 8000 }] } }
  vi.mocked(api.loadRegionalReview).mockResolvedValue(review)
  vi.mocked(api.saveRegionalReview).mockImplementation(async body => ({ revision: 2, sha256: 'f'.repeat(64), created_at: review.created_at, request: body }))
  vi.mocked(api.previewRegionalDataset).mockResolvedValue({ review_revision: 2, review_sha256: 'f'.repeat(64), can_prepare: true,
    source_bytes: 16044, excerpt_bytes: 36000, required_free_bytes: 100000, free_bytes: 1000000,
    split: { recordings: [{ recording_id: 'b'.repeat(32), cluster_sha256: 'a'.repeat(64), split: 'train',
      adapter_distribution_permitted: false, source_redistribution_permitted: false }], independent_groups: 1,
      training_recordings: 1, holdout_recordings: 0, evaluation_sufficient: false, warnings: ['independent_groups_insufficient'] } })
  const node = await mount()
  button(node, 'Dataset A').click(); await settle(); button(node, 'Inspect recording').click(); await settle()
  const original = [...node.querySelectorAll('label')].find(value => value.textContent?.includes('I confirm this file is the full original'))?.querySelector('input')
  if (!(original instanceof HTMLInputElement)) throw new Error('Missing original declaration')
  original.checked = false; original.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  button(node, 'Save review revision').click(); await settle()
  expect(vi.mocked(api.saveRegionalReview).mock.calls[0]?.[0].recordings[0]?.original_recording_sha256).toBeNull()
  button(node, 'Review split and storage').click(); await settle()
  expect(button(node, 'Prepare retained copies').disabled).toBe(true)
  const label = [...node.querySelectorAll('label')].find(value => value.textContent?.includes('I reviewed this saved revision'))
  const checkbox = label?.querySelector('input')
  if (!(checkbox instanceof HTMLInputElement)) throw new Error('Missing storage confirmation')
  checkbox.checked = true; checkbox.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(button(node, 'Prepare retained copies').disabled).toBe(false)
  const caption = node.querySelector('textarea')
  if (!(caption instanceof HTMLTextAreaElement)) throw new Error('Missing caption')
  caption.value = 'An edited description'; caption.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(node.textContent).not.toContain('Additional free space required:')
  expect(api.prepareRegionalDataset).not.toHaveBeenCalled()
})

it('saves acoustic grouping through a new review revision before any preparation or training', async () => {
  const review = acousticReviewFixture('a'.repeat(32), 1), job = acousticJobFixture('a'.repeat(32), 1)
  vi.mocked(api.loadRegionalReview).mockResolvedValue(review)
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [job]})
  vi.mocked(api.saveRegionalReview).mockImplementation(async request => ({revision: 2, sha256: 'f'.repeat(64), created_at: review.created_at, request}))
  const node = await mount()
  button(node, 'Dataset A').click(); await settle()
  button(node, 'Use this scan for the dataset split').click(); await settle()
  expect(api.saveRegionalReview).toHaveBeenCalledWith(expect.objectContaining({expected_revision: 1,
    acoustic_review: {scan_id: job.id, scan_sha256: job.result?.sha256, decisions: []}, recordings: review.request.recordings,
    excerpts: review.request.excerpts}), expect.any(AbortSignal))
  expect(node.textContent).toContain('The scan and decisions were saved')
  expect(api.prepareRegionalDataset).not.toHaveBeenCalled(); expect(api.submitRegionalTraining).not.toHaveBeenCalled()
})

it('hides arm names during an unlabeled review and reveals them only after save', async () => {
  const preparationId = '1'.repeat(32), datasetId = 'a'.repeat(32), comparisonId = 'b'.repeat(32)
  const manifestSha = '9'.repeat(64), planSha = 'c'.repeat(64), baseHash = '1'.repeat(64), adapterHash = '2'.repeat(64)
  const review: RegionalReview = { revision: 1, sha256: 'e'.repeat(64), created_at: '2026-10-17T00:00:00Z', request: {
    dataset_id: datasetId, dataset_revision: 1, expected_revision: 0, tradition: 'Fixture tradition', recordings: [], excerpts: [] } }
  const prepared: RegionalPreparationJob = { id: preparationId, status: 'done', created_at: review.created_at, request: {
    dataset_id: datasetId, review_revision: 1, review_sha256: review.sha256, submission_key: 'f'.repeat(32) }, result: { sha256: manifestSha, manifest: {
      id: preparationId, dataset_id: datasetId, review_revision: 1, review_sha256: review.sha256, created_at: review.created_at, tradition: review.request.tradition,
      split_seed: 42, recordings: [], excerpts: [], split: { recordings: [], independent_groups: 0, training_recordings: 0, holdout_recordings: 0, evaluation_sufficient: false, warnings: [] } } } }
  const state: RegionalComparisonState = { listener_reviews: 1, blind_reviews: 0, arms: [
    { arm: 'base', output_sha256: baseHash, audio_url: `/api/music/regional-datasets/comparisons/${comparisonId}/audio/base` },
    { arm: 'adapter', output_sha256: adapterHash, audio_url: `/api/music/regional-datasets/comparisons/${comparisonId}/audio/adapter` } ],
  plan: { sha256: planSha, spec: { id: comparisonId, created_at: review.created_at, held_out_excerpt_ids: [], publisher_declarations: {}, quality_status: 'unverified',
    request: { manifest_id: preparationId, manifest_sha256: manifestSha, submission_key: 'a'.repeat(32), adapter_id: 'd'.repeat(32), adapter_sha256: '8'.repeat(64),
      request: { engine: 'stable_audio3', prompt: 'Held-out phrase', duration_seconds: 5, seed: 7 } },
    adapter: { id: 'd'.repeat(32), engine: 'stable_audio3', target: 'small-music', name: 'Fixture', sha256: '8'.repeat(64), created_at: review.created_at },
    training_job_id: 'e'.repeat(32), training_request: { name: 'Fixture', samples: [{ caption: 'Reviewed phrase' }] },
    training_receipt: { job_id: 'e'.repeat(32), request_sha256: '7'.repeat(64), dataset_sha256: '6'.repeat(64), identity_status: 'unavailable' } } } }
  vi.mocked(api.getRegionalPreparation).mockResolvedValue(prepared)
  vi.mocked(api.loadRegionalReview).mockResolvedValue(review)
  vi.mocked(api.regionalTrainingSelection).mockResolvedValue({ manifest_id: preparationId, manifest_sha256: manifestSha, samples: [], held_out_excerpts: 0, evaluation_sufficient: false, adapter_distribution_permitted: false })
  vi.mocked(api.listRegionalComparisons).mockResolvedValue({ comparisons: [state] })
  vi.mocked(api.openRegionalBlindSession).mockResolvedValue({ comparison_id: comparisonId, comparison_sha256: planSha, reviewer_reference: 'listener-one', assignment_sha256: 'd'.repeat(64), clips: [
    { label: 'a', output_sha256: adapterHash, audio_url: `/api/music/regional-datasets/comparisons/${comparisonId}/blind/listener-one/audio/a` },
    { label: 'b', output_sha256: baseHash, audio_url: `/api/music/regional-datasets/comparisons/${comparisonId}/blind/listener-one/audio/b` } ] })
  vi.mocked(api.saveRegionalBlindReview).mockResolvedValue({ comparison_id: comparisonId, reviewer_reference: 'listener-one', label_a_arm: 'adapter', label_b_arm: 'base', decoded_preference: 'adapter', quality_status: 'unverified', blind_reviews: 1 })
  vi.mocked(api.getRegionalComparison).mockResolvedValue({ ...state, blind_reviews: 1 })
  localStorage.setItem('openfabric-regional-preparation-v1', preparationId)
  const node = await mount()
  expect(node.querySelector('audio[aria-label="Base"]')).toBeTruthy()
  expect(node.textContent).toContain('1 saved identified listener reviews and 0 saved unlabeled reviews')
  const listener = labeled(node, 'Private reviewer reference'), scope = labeled(node, 'Musician/reviewer tradition'), heard = labeled(node, 'I listened to both exact retained')
  listener.value = 'listener-one'; listener.dispatchEvent(new Event('input', { bubbles: true }))
  scope.value = 'Fixture scope'; scope.dispatchEvent(new Event('input', { bubbles: true }))
  heard.checked = true; heard.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(button(node, 'Hide arm labels and review A/B').disabled).toBe(false)
  button(node, 'Hide arm labels and review A/B').click(); await settle()
  const clips = [...node.querySelectorAll('audio')]
  expect(clips.map(item => item.getAttribute('aria-label'))).toEqual(['Clip A', 'Clip B'])
  expect(clips.every(item => !item.getAttribute('src')?.includes('/audio/base') && !item.getAttribute('src')?.includes('/audio/adapter'))).toBe(true)
  expect(node.textContent).not.toContain('Show source-bound model card')
  expect(node.textContent).not.toContain('Save listening review against these exact outputs')
  button(node, 'Show arm labels and end this blind presentation').click(); await settle()
  expect(node.textContent).toContain('Arm labels were opened after this assignment')
  expect(node.querySelector('audio[aria-label="Base"]')).toBeTruthy()
  button(node, 'Hide arm labels and review A/B').click(); await settle()
  expect(node.textContent).not.toContain('Arm labels were opened after this assignment')
  button(node, 'Save unlabeled review and reveal the assignment').click(); await settle()
  const saved = vi.mocked(api.saveRegionalBlindReview).mock.calls[0]?.[1]
  expect(saved?.preference).toBe('tie')
  expect(saved?.label_a_sha256).toBe(adapterHash)
  expect(saved && 'base_output_sha256' in saved).toBe(false)
  expect(node.textContent).toContain('Clip A was Adapter. Clip B was Base. The saved preference decodes to Adapter. Quality remains unverified.')
  expect(node.textContent).toContain('1 saved identified listener reviews and 1 saved unlabeled reviews')
  vi.mocked(api.getRegionalModelCard).mockResolvedValue(modelCard(comparisonId))
  button(node, 'Show source-bound model card').click(); await settle()
  expect(node.textContent).toContain('Quality is unverified.')
  expect(node.textContent).toContain('The saved split does not mark adapter distribution as permitted.')
  expect(node.textContent).toContain('Example performer, used with scoped permission.')
  expect(node.textContent).not.toContain('fixture-contract')
})

function modelCard(comparisonId: string): RegionalModelCard {
  return { comparison_id: comparisonId, comparison_sha256: 'c'.repeat(64), quality_status: 'unverified', distribution_status: 'not_approved',
    notices: [
      { code: 'quality_unverified', text: 'Quality is unverified.' },
      { code: 'metrics_unavailable', text: 'FAD, CLAP, instrument classification and cultural authenticity were not measured.' },
      { code: 'listening_not_a_panel', text: 'Saved listening reviews are local records. They are not a recruited panel or a recommendation.' },
      { code: 'generation_runtime_unverified', text: 'The generation runtime identity was not measured.' },
      { code: 'distribution_not_approved', text: 'This card does not approve distribution, commercial use, or release.' } ],
    tradition: 'Fixture tradition', manifest_id: '1'.repeat(32), manifest_sha256: '9'.repeat(64), training_recordings: 1, holdout_recordings: 1,
    excluded_recordings: 1, training_excerpts: 1, held_out_excerpts: 1, declared_adapter_distribution_permitted: false, declared_source_redistribution_permitted: false,
    adapter_id: 'd'.repeat(32), adapter_name: 'Fixture', adapter_sha256: '8'.repeat(64), adapter_target: 'small-music', training_rank: 8, training_max_steps: 1,
    training_learning_rate: 0.0001, training_seed: 1, training_request_sha256: '7'.repeat(64), training_dataset_sha256: '6'.repeat(64), training_identity_status: 'unavailable',
    prompt: 'Held-out phrase', comparison_seed: 7, duration_seconds: 5, listener_reviews: 1, blind_reviews: 1,
    attributions: [{ source: 'rights_record', text: 'Example performer, used with scoped permission.' }], hashes: [] }
}

function labeled(node: ParentNode, caption: string): HTMLInputElement {
  const input = [...node.querySelectorAll('label')].find(item => item.textContent?.includes(caption))?.querySelector('input')
  if (!(input instanceof HTMLInputElement)) throw new Error(`Missing ${caption}`)
  return input
}
