// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, ref, type App } from 'vue'
import { createPinia, type Pinia } from 'pinia'
import { useLoraDatasetWorkspace } from './useLoraDatasetWorkspace'
import { useLoraTrainingStore } from '../stores/loraTraining'
import * as api from '../api/loraDataset'
import * as native from '../api/aceStepTraining'
import type { LoraDatasetEntry, LoraDatasetOperationResponse, LoraWorkspace, LoraWorkspaceForm } from '../api/generated'
import { i18n } from '../i18n'
import { ApiError } from '../api/http'

vi.mock('../api/loraDataset', async original => ({ ...await original<typeof import('../api/loraDataset')>(),
  listLoraDatasets: vi.fn(), getLoraWorkspace: vi.fn(), saveLoraWorkspace: vi.fn(), createLoraDataset: vi.fn(),
  renameLoraDataset: vi.fn(), trashLoraDataset: vi.fn(), restoreLoraDataset: vi.fn(), loadLoraDataset: vi.fn(),
  saveLoraDataset: vi.fn(), scanLoraDataset: vi.fn(), uploadDatasetFiles: vi.fn(), updateLoraDatasetSample: vi.fn(),
}))
vi.mock('../api/aceStepTraining', async original => ({ ...await original<typeof import('../api/aceStepTraining')>(),
  trainingStatus: vi.fn(), autoLabelStatus: vi.fn(), preprocessStatus: vi.fn(), latestAutoLabelStatus: vi.fn(),
  latestPreprocessStatus: vi.fn(), getSamples: vi.fn(), startAutoLabel: vi.fn(), startPreprocess: vi.fn(),
}))
const selected: LoraDatasetEntry = { id: 'a'.repeat(32), name: 'Selected', dataset_path: 'datasets/selected/dataset.json', audio_dir: 'datasets/selected', sample_count: 1, labeled_count: 1, revision: 1, trashed: false, available: true }
const other: LoraDatasetEntry = { ...selected, id: 'b'.repeat(32), name: 'Other', dataset_path: 'datasets/other/dataset.json', audio_dir: 'datasets/other' }
const sample: native.DatasetSample = { index: 0, filename: 'voice.wav', audio_path: 'datasets/selected/voice.wav', duration: 10, caption: 'preserved', genre: '', prompt_override: null, lyrics: '', bpm: null, keyscale: '', timesignature: '', language: 'en', is_instrumental: false, labeled: true }
const idle = { task_id: null, status: 'idle', progress: '', current: 0, total: 0 } as const
const training: native.TrainingStatus = { is_training: false, should_stop: false, current_step: 0, current_loss: null, status: 'Idle', config: {}, tensor_dir: '', loss_history: [], tensorboard_url: null, tensorboard_logdir: null, training_log: '', start_time: null, current_epoch: 0, steps_per_second: 0, estimated_time_remaining: 0, error: null }
function operation(entry = selected): LoraDatasetOperationResponse { return { dataset: entry, samples: [{ ...sample, id: 'sample-one', audio_path: `${entry.audio_dir}/voice.wav` }], message: 'Loaded' } }
function defaults(): Required<LoraWorkspaceForm> {
  return { audio_dir: 'datasets/my-voice', custom_tag: '', tag_position: 'replace', all_instrumental: true, skip_metas: false, format_lyrics: false, transcribe_lyrics: false, only_unlabeled: true, genre_ratio: 0, output_dir: '', skip_existing: false, tensor_dir: '', lora_output_dir: './lora_output', lora_rank: 64, lora_alpha: 128, lora_dropout: 0.1, learning_rate: 0.0001, train_epochs: 10, train_batch_size: 1, gradient_accumulation: 4, save_every_n_epochs: 5, training_seed: 42, use_fp8: false, gradient_checkpointing: false, export_path: '', registry_name: '' }
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>(release => { resolve = release }); return { promise, resolve }
}
let engineRunning = true
let pinia: Pinia, app: App | undefined, form: Required<LoraWorkspaceForm>, library: ReturnType<typeof useLoraDatasetWorkspace>, workspace: LoraWorkspace
async function settle() { for (let i = 0; i < 20; i++) await nextTick() }
async function mount() {
  form = reactive(defaults())
  app = createApp(defineComponent({ setup() { library = useLoraDatasetWorkspace(useLoraTrainingStore(pinia), form, ref(engineRunning)); return () => h('div') } })).use(pinia).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')))
  return library.initialize()
}
function unmount() { app?.unmount(); app = undefined; useLoraTrainingStore(pinia).stopBackgroundTasks(); document.body.replaceChildren() }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); pinia = createPinia(); engineRunning = true
  workspace = { revision: 1, selected_dataset_id: selected.id, form: defaults() }
  vi.mocked(api.listLoraDatasets).mockResolvedValue({ datasets: [selected, other] })
  vi.mocked(api.getLoraWorkspace).mockImplementation(async () => structuredClone(workspace))
  vi.mocked(api.saveLoraWorkspace).mockImplementation(async request => {
    if (request.revision !== workspace.revision) throw new ApiError('workspace_revision_conflict', 409)
    workspace = { ...request, revision: (request.revision ?? 0) + 1 }
    return structuredClone(workspace)
  })
  vi.mocked(api.loadLoraDataset).mockImplementation(async id => operation(id === selected.id ? selected : other))
  vi.mocked(native.latestAutoLabelStatus).mockResolvedValue(idle)
  vi.mocked(native.latestPreprocessStatus).mockResolvedValue(idle)
  vi.mocked(native.trainingStatus).mockResolvedValue(training)
  vi.mocked(native.getSamples).mockResolvedValue({ samples: [sample] })
})
afterEach(() => { unmount(); vi.clearAllTimers(); vi.useRealTimers() })

it('ignores a dataset load response that arrives after initialization was unmounted', async () => {
  const response = deferred<LoraDatasetOperationResponse>()
  vi.mocked(api.loadLoraDataset).mockReturnValue(response.promise)
  const initialization = mount(); await settle(); unmount()
  response.resolve(operation()); await initialization; await settle()
  expect(useLoraTrainingStore(pinia).samples).toEqual([])
})

it('does not restart observers when a selected dataset load returns after teardown', async () => {
  await mount()
  const response = deferred<LoraDatasetOperationResponse>()
  vi.mocked(api.loadLoraDataset).mockReturnValue(response.promise)
  const selection = library.select(other.id); await settle(); unmount()
  const calls = vi.mocked(native.trainingStatus).mock.calls.length
  response.resolve(operation(other)); await selection; await settle()
  expect(useLoraTrainingStore(pinia).samples).toEqual([])
  expect(native.trainingStatus).toHaveBeenCalledTimes(calls)
  expect(useLoraTrainingStore(pinia)._backgroundWatching).toBe(false)
})

it('restores a newer backend task on re-entry despite a retained idle training response', async () => {
  await mount(); unmount()
  workspace = { ...workspace, revision: 2, preprocess_task: { task_id: 'new-preprocess', dataset_id: selected.id, dataset_path: selected.dataset_path, output_dir: `${selected.audio_dir}/tensors` } }
  const active: native.PreprocessStatus = { task_id: 'new-preprocess', status: 'running', progress: '', current: 2, total: 4 }
  vi.mocked(native.latestPreprocessStatus).mockResolvedValue(active)
  vi.mocked(native.preprocessStatus).mockResolvedValue(active)
  await mount()
  expect(useLoraTrainingStore(pinia).preprocessTaskId).toBe('new-preprocess')
  expect(useLoraTrainingStore(pinia).preprocessRunning).toBe(true)
  expect(useLoraTrainingStore(pinia).samples).toEqual([sample])
})

it('keeps mismatched saved preprocessing outside the selected dataset context', async () => {
  workspace.preprocess_task = { task_id: 'other-preprocess', dataset_id: other.id, dataset_path: other.dataset_path, output_dir: `${other.audio_dir}/tensors` }
  const active: native.PreprocessStatus = { task_id: 'other-preprocess', status: 'running', progress: '', current: 2, total: 4 }
  vi.mocked(native.latestPreprocessStatus).mockResolvedValue(active)
  vi.mocked(native.preprocessStatus).mockResolvedValue(active)
  await mount()
  const store = useLoraTrainingStore(pinia)
  expect(store.preprocessTaskId).toBe('')
  expect(store.preprocessRunning).toBe(false)
  expect(store.samples).toEqual([])
  expect(store.discoveredPreprocess?.task_id).toBe('other-preprocess')
  expect(native.getSamples).not.toHaveBeenCalled()
})

it('recovers a backend-recorded label whose acknowledgement never reached the previous browser', async () => {
  workspace.auto_label_task = { task_id: 'accepted-label', dataset_id: selected.id, dataset_path: selected.dataset_path }
  const active: native.AutoLabelStatus = { task_id: 'accepted-label', status: 'running', progress: 'Labeling', current: 3, total: 4, save_path: selected.dataset_path }
  vi.mocked(native.autoLabelStatus).mockResolvedValue(active)
  await mount()
  const store = useLoraTrainingStore(pinia)
  expect(store.autoLabelTaskId).toBe('accepted-label')
  expect(store.autoLabelCurrent).toBe(3)
  expect(store.samples).toEqual([sample])
  expect(native.startAutoLabel).not.toHaveBeenCalled()
  expect(api.loadLoraDataset).not.toHaveBeenCalled()
})

it('flushes edited form settings when navigating before the debounce deadline', async () => {
  await mount()
  form.lora_rank = 128; form.custom_tag = 'new-voice'; await settle(); unmount(); await settle()
  await mount()
  expect(form.lora_rank).toBe(128)
  expect(form.custom_tag).toBe('new-voice')
})

it('saves normal form edits with their workspace revision', async () => {
  await mount()
  form.train_epochs = 23; await settle(); await vi.advanceTimersByTimeAsync(400)
  expect(workspace.form?.train_epochs).toBe(23)
  expect(api.saveLoraWorkspace).toHaveBeenCalledWith(expect.objectContaining({ revision: 1, selected_dataset_id: selected.id, form: expect.objectContaining({ train_epochs: 23 }) }), expect.any(AbortSignal), false)
})

it('reconciles the workspace revision changed by trashing its selected dataset', async () => {
  await mount()
  vi.mocked(api.trashLoraDataset).mockImplementation(async () => {
    workspace = { ...workspace, selected_dataset_id: null, revision: (workspace.revision ?? 0) + 1 }
    return { ...selected, trashed: true, revision: selected.revision + 1 }
  })
  await library.trash(); await settle()
  expect(library.selectedId.value).toBeNull()
  expect(library.error.value).toBe('')
  expect(workspace.selected_dataset_id).toBeNull()
})

it('rejects another tab workspace changes without overwriting its saved settings', async () => {
  await mount()
  workspace = { ...workspace, revision: 4, form: { ...workspace.form, lora_rank: 32 } }
  form.lora_rank = 128; await settle(); await vi.advanceTimersByTimeAsync(400)
  expect(workspace.form?.lora_rank).toBe(32)
  expect(library.error.value).toContain('changed')
})

it('preserves saved workspace form choices when loading its dataset metadata on reload', async () => {
  workspace.form = { ...workspace.form, custom_tag: 'new-voice', genre_ratio: 35, all_instrumental: false }
  vi.mocked(api.loadLoraDataset).mockResolvedValue({ ...operation(), metadata: { custom_tag: 'old-voice', genre_ratio: 0, all_instrumental: true, tag_position: 'replace' } })
  await mount()
  expect(form.custom_tag).toBe('new-voice')
  expect(form.genre_ratio).toBe(35)
  expect(form.all_instrumental).toBe(false)
})

it('does not apply an old snapshot response over a different selected dataset', async () => {
  await mount()
  const response = deferred<LoraDatasetOperationResponse>()
  vi.mocked(api.saveLoraDataset).mockReturnValue(response.promise)
  const snapshot = library.saveSnapshot(); await settle()
  await library.select(other.id)
  const selectedBefore = library.selectedId.value, pathBefore = useLoraTrainingStore(pinia).datasetPath
  response.resolve({ ...operation(), dataset: { ...selected, revision: selected.revision + 1 } }); await snapshot
  expect(library.selectedId.value).toBe(selectedBefore)
  expect(useLoraTrainingStore(pinia).datasetPath).toBe(pathBefore)
})

it('does not restart observers when navigation happens during selection workspace persistence', async () => {
  await mount()
  const response = deferred<LoraWorkspace>()
  vi.mocked(api.saveLoraWorkspace).mockReturnValueOnce(response.promise)
  const selection = library.select(other.id); await settle(); unmount()
  const calls = vi.mocked(native.trainingStatus).mock.calls.length
  response.resolve({ ...workspace, selected_dataset_id: other.id, revision: 2 }); await selection; await settle()
  expect(native.trainingStatus).toHaveBeenCalledTimes(calls)
  expect(useLoraTrainingStore(pinia)._backgroundWatching).toBe(false)
})

it('keeps upload and scan owned by one dataset while selection is disabled', async () => {
  await mount()
  const response = deferred<api.UploadDatasetFilesResponse>()
  const files = [new File(['audio'], 'new.wav', { type: 'audio/wav' })]
  vi.mocked(api.uploadDatasetFiles).mockReturnValue(response.promise)
  vi.mocked(api.scanLoraDataset).mockResolvedValue({ ...operation(), dataset: { ...selected, revision: 2 } })
  const upload = library.upload(files); await settle()
  expect(library.disabled.value).toBe(true)
  await library.select(other.id)
  expect(library.selectedId.value).toBe(selected.id)
  response.resolve({ audio_dir: selected.audio_dir, saved: ['new.wav'], skipped: [] }); await upload
  expect(api.uploadDatasetFiles).toHaveBeenCalledWith(selected.name, files, selected.id, selected.revision, expect.any(AbortSignal))
  expect(api.scanLoraDataset).toHaveBeenCalledWith(selected.id, expect.objectContaining({ audio_dir: selected.audio_dir, revision: 1 }), expect.any(AbortSignal))
  expect(useLoraTrainingStore(pinia).datasetPath).toBe(selected.dataset_path)
})

it('does not scan a completed upload after its page was unmounted', async () => {
  await mount()
  const response = deferred<api.UploadDatasetFilesResponse>()
  vi.mocked(api.uploadDatasetFiles).mockReturnValue(response.promise)
  const upload = library.upload([new File(['audio'], 'new.wav')]); await settle(); unmount()
  response.resolve({ audio_dir: 'datasets/late', saved: ['new.wav'], skipped: [] })
  await expect(upload).resolves.toBeUndefined()
  expect(api.scanLoraDataset).not.toHaveBeenCalled()
  expect(form.audio_dir).toBe(selected.audio_dir)
  expect(vi.mocked(api.uploadDatasetFiles).mock.calls[0]?.[4]?.aborted).toBe(true)
})

it('does not apply a manual sample save after its page was unmounted', async () => {
  await mount()
  const response = deferred<LoraDatasetOperationResponse>()
  vi.mocked(api.updateLoraDatasetSample).mockReturnValue(response.promise)
  const update = library.updateSample(0, { caption: 'late-caption' }); await settle(); unmount()
  const result = operation()
  result.samples[0] = { ...result.samples[0], id: 'sample-one', caption: 'late-caption' }
  response.resolve(result); await update
  expect(useLoraTrainingStore(pinia).samples[0]?.caption).toBe('preserved')
})

it('persists the observed training tensor identity independently of editable form settings', async () => {
  await mount()
  useLoraTrainingStore(pinia).trainingTensorDir = 'datasets/selected/active-tensors'
  form.tensor_dir = 'datasets/selected/next-tensors'
  await library.persist()
  expect(workspace.training_tensor_dir).toBe('datasets/selected/active-tensors')
  expect(workspace.form?.tensor_dir).toBe('datasets/selected/next-tensors')
})

it('keeps offline catalog controls enabled when native status correctly reports an inactive engine', async () => {
  engineRunning = false
  vi.mocked(native.latestAutoLabelStatus).mockRejectedValue(new ApiError('model_inactive', 503))
  vi.mocked(native.latestPreprocessStatus).mockRejectedValue(new ApiError('model_inactive', 503))
  vi.mocked(native.trainingStatus).mockRejectedValue(new ApiError('model_inactive', 503))
  await mount()
  expect(library.ready.value).toBe(true)
  expect(library.disabled.value).toBe(false)
  expect(api.loadLoraDataset).not.toHaveBeenCalled()
})

it('does not begin a queued dataset selection after its page has unmounted', async () => {
  await mount()
  const response = deferred<LoraWorkspace>()
  vi.mocked(api.saveLoraWorkspace).mockReturnValueOnce(response.promise)
  const persistence = library.persist(); await settle()
  const selection = library.select(other.id); await settle(); unmount()
  useLoraTrainingStore(pinia).datasetPath = 'datasets/new-page/dataset.json'
  response.resolve({ ...workspace, revision: 2 }); await persistence; await selection; await settle()
  expect(useLoraTrainingStore(pinia).datasetPath).toBe('datasets/new-page/dataset.json')
  expect(api.loadLoraDataset).toHaveBeenCalledOnce()
})
