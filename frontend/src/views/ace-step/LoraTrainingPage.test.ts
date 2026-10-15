// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createPinia, type Pinia } from 'pinia'
import LoraTrainingPage from './LoraTrainingPage.vue'
import { useLoraTrainingStore } from '../../stores/loraTraining'
import { useOrchestratorStore } from '../../stores/orchestrator'
import * as api from '../../api/aceStepTraining'
import * as ace from '../../api/aceStep'
import { i18n } from '../../i18n'
import { ApiError } from '../../api/http'
import * as datasets from '../../api/loraDataset'

vi.mock('../../api/aceStepTraining', async original => ({ ...await original<typeof import('../../api/aceStepTraining')>(), trainingStatus: vi.fn(), autoLabelStatus: vi.fn(), preprocessStatus: vi.fn(), latestAutoLabelStatus: vi.fn(), latestPreprocessStatus: vi.fn(), getSamples: vi.fn(), startAutoLabel: vi.fn(), startLoraTraining: vi.fn() }))
vi.mock('../../api/aceStep', async original => ({ ...await original<typeof import('../../api/aceStep')>(), health: vi.fn(), initModel: vi.fn() }))
vi.mock('../../api/loraDataset', () => ({ listLoraDatasets: vi.fn(), getLoraWorkspace: vi.fn(), saveLoraWorkspace: vi.fn(), createLoraDataset: vi.fn(), renameLoraDataset: vi.fn(), trashLoraDataset: vi.fn(), restoreLoraDataset: vi.fn(), loadLoraDataset: vi.fn(), scanLoraDataset: vi.fn(), saveLoraDataset: vi.fn(), preprocessLoraDataset: vi.fn(), autoLabelLoraDataset: vi.fn(), updateLoraDatasetSample: vi.fn(), uploadDatasetFiles: vi.fn() }))
let app: App | undefined, pinia: Pinia
const training: api.TrainingStatus = { is_training: true, should_stop: false, current_step: 1, current_loss: 0.2, status: 'Running', config: { epochs: 3 }, tensor_dir: 'tensors', loss_history: [], tensorboard_url: null, tensorboard_logdir: null, training_log: '', start_time: 1, current_epoch: 1, steps_per_second: 1, estimated_time_remaining: 30, error: null }
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>(release => { resolve = release }); return { promise, resolve }
}
async function settle() { for (let i = 0; i < 24; i++) await nextTick() }
async function mount() { app = createApp(LoraTrainingPage).use(pinia).use(i18n); app.mount(document.body.appendChild(document.createElement('div'))); await settle() }
function unmount() { app?.unmount(); app = undefined; document.body.replaceChildren() }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); pinia = createPinia()
  const orchestrator = useOrchestratorStore(pinia)
  orchestrator.statuses.ace_step = { id: 'ace_step', label: 'ACE-Step', status: 'running', error: null }
  vi.mocked(ace.health).mockResolvedValue({ status: 'ok', service: 'ACE', version: '1', models_initialized: true, llm_initialized: true, loaded_model: 'model', loaded_lm_model: 'lm' })
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, is_training: false })
  const idle = { task_id: null, status: 'idle', progress: '', current: 0, total: 0 } as const
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(idle)
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue(idle)
  vi.mocked(api.getSamples).mockResolvedValue({ samples: [] })
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [], issues: [] })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 0, selected_dataset_id: null, form: {} })
  vi.mocked(datasets.saveLoraWorkspace).mockImplementation(async workspace => ({ ...workspace, revision: (workspace.revision ?? 0) + 1 }))
})

const savedDataset = { id: 'a'.repeat(32), name: 'Saved voice', dataset_path: 'datasets/voice/dataset.json', audio_dir: 'datasets/voice', sample_count: 22, labeled_count: 22, revision: 1, trashed: false, available: true, error_code: '' }
const sample = { id: 'sample-one', index: 0, filename: 'voice.wav', audio_path: 'datasets/voice/voice.wav', duration: 10, caption: 'Original', genre: '', prompt_override: null, lyrics: '', bpm: null, keyscale: '', timesignature: '', language: 'en', is_instrumental: false, labeled: true }
async function editingRows() {
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [savedDataset] })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, selected_dataset_id: savedDataset.id, form: {} })
  vi.mocked(datasets.loadLoraDataset).mockResolvedValue({ dataset: savedDataset, samples: [sample, { ...sample, id: 'sample-two', index: 1, filename: 'other.wav', audio_path: 'datasets/voice/other.wav' }] })
  await mount()
  const rows = [...document.querySelectorAll('tbody tr')]
  const fields = rows.map(row => row.querySelector('input[type="text"]'))
  for (const [index, field] of fields.entries()) {
    if (!(field instanceof HTMLInputElement)) throw new Error('Missing caption')
    field.value = `Edited ${index}`; field.dispatchEvent(new Event('input'))
  }
  await settle(); return { rows, fields }
}
it('preserves the caption draft when its save is rejected', async () => {
  const { rows, fields } = await editingRows()
  vi.mocked(datasets.updateLoraDatasetSample).mockRejectedValue(new ApiError('dataset_revision_conflict', 409))
  rows[0]?.querySelector('button')?.click(); await settle()
  expect(fields[0]).toHaveProperty('value', 'Edited 0')
})
it('preserves drafts in other rows when one caption saves successfully', async () => {
  const { rows, fields } = await editingRows()
  vi.mocked(datasets.updateLoraDatasetSample).mockResolvedValue({ dataset: { ...savedDataset, revision: 2 }, samples: [{ ...sample, caption: 'Edited 0' }, { ...sample, id: 'sample-two', index: 1, filename: 'other.wav', audio_path: 'datasets/voice/other.wav' }] })
  rows[0]?.querySelector('button')?.click(); await settle()
  expect(fields[1]).toHaveProperty('value', 'Edited 1')
  expect(useLoraTrainingStore(pinia).samples[0]?.caption).toBe('Edited 0')
})
it('shows live training progress and Stop after reload without loading the busy native builder', async () => {
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [savedDataset] })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, selected_dataset_id: savedDataset.id, training_tensor_dir: training.tensor_dir, form: { tensor_dir: training.tensor_dir } })
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, current_step: 29 })
  await mount()
  expect(useLoraTrainingStore(pinia).samples).toEqual([])
  expect(document.body.textContent).toContain(i18n.global.t('lora.step5Title'))
  expect([...document.querySelectorAll('button')].some(button => button.textContent?.trim() === i18n.global.t('lora.stop'))).toBe(true)
  expect(document.body.textContent).toContain('In use')
  expect(datasets.loadLoraDataset).not.toHaveBeenCalled()
})
it('restores a saved dataset by loading its labels without rescanning', async () => {
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [savedDataset] })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, selected_dataset_id: savedDataset.id, form: { custom_tag: 'my-voice' } })
  vi.mocked(datasets.loadLoraDataset).mockResolvedValue({ dataset: savedDataset, samples: [], message: 'Loaded' })
  await mount()
  expect(document.body.textContent).toContain('Saved voice')
  expect(datasets.loadLoraDataset).toHaveBeenCalledWith(savedDataset.id, 1, expect.any(AbortSignal))
  expect(datasets.scanLoraDataset).not.toHaveBeenCalled()
  expect(useLoraTrainingStore(pinia).datasetPath).toBe(savedDataset.dataset_path)
})
it('keeps the dataset library available while the engine is stopped', async () => {
  useOrchestratorStore(pinia).statuses.ace_step = { id: 'ace_step', label: 'ACE-Step', status: 'stopped', error: null }
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [savedDataset] })
  await mount()
  expect(document.body.textContent).toContain('Saved voice')
  expect(document.querySelector('select[aria-label="Saved dataset"]')).not.toBeNull()
})
afterEach(() => { unmount(); useLoraTrainingStore(pinia).stopBackgroundTasks(); vi.clearAllTimers(); vi.useRealTimers(); localStorage.clear() })

it('translates a native operation failure in the training store', async () => {
  vi.mocked(api.startAutoLabel).mockRejectedValueOnce(new ApiError('native_operation_failed', 500))
  await useLoraTrainingStore(pinia).startAutoLabel({})
  expect(useLoraTrainingStore(pinia).autoLabelError).toBe('The native model could not complete this operation. Check the engine and try again.')
})

it('translates a native initialization failure instead of displaying its internal code', async () => {
  vi.mocked(ace.health).mockResolvedValue({ status: 'ok', service: 'ACE', version: '1', models_initialized: false, llm_initialized: false, loaded_model: null, loaded_lm_model: null })
  vi.mocked(ace.initModel).mockRejectedValueOnce(new ApiError('native_operation_failed', 500))
  await mount()
  const initialize = [...document.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('lora.initModel'))
  if (!initialize) throw new Error('Missing initialization action')
  initialize.click(); await settle()
  expect(document.body.textContent).toContain('The native model could not complete this operation')
  expect(document.body.textContent).not.toContain('native_operation_failed')
})

it('resumes observation of active training on re-entry without submitting new work', async () => {
  vi.mocked(api.trainingStatus).mockResolvedValue(training)
  await mount()
  expect(document.body.textContent).not.toContain('Its dataset could not be matched')
  unmount()
  await vi.advanceTimersByTimeAsync(9000)
  expect(api.trainingStatus).toHaveBeenCalledOnce()
  await mount()
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, is_training: false, current_step: 20, status: 'Complete' })
  await vi.advanceTimersByTimeAsync(3000)
  expect(useLoraTrainingStore(pinia).isTraining).toBe(false)
  expect(useLoraTrainingStore(pinia).training?.current_step).toBe(20)
  expect(api.trainingStatus).toHaveBeenCalledTimes(3)
})

it('resumes retained auto-label and preprocessing tasks on re-entry', async () => {
  const store = useLoraTrainingStore(pinia)
  store.autoLabelRunning = true; store.autoLabelTaskId = 'label-task'
  store.preprocessRunning = true; store.preprocessTaskId = 'prepare-task'
  vi.mocked(api.autoLabelStatus).mockResolvedValue({ task_id: 'label-task', status: 'running', current: 1, total: 2, progress: '' })
  vi.mocked(api.preprocessStatus).mockResolvedValue({ task_id: 'prepare-task', status: 'running', current: 1, total: 2, progress: '' })
  await mount(); unmount()
  vi.mocked(api.autoLabelStatus).mockResolvedValue({ task_id: 'label-task', status: 'completed', current: 2, total: 2, progress: '' })
  vi.mocked(api.preprocessStatus).mockResolvedValue({ task_id: 'prepare-task', status: 'completed', current: 2, total: 2, progress: '', result: { output_dir: 'completed/tensors', num_tensors: 2, message: '' } })
  await mount()
  expect(store.autoLabelRunning).toBe(false)
  expect(store.preprocessRunning).toBe(false)
  expect(store.preprocessOutputDir).toBe('completed/tensors')
  expect(api.autoLabelStatus).toHaveBeenCalledTimes(2)
  expect(api.preprocessStatus).toHaveBeenCalledTimes(2)
})

it('invalidates a pending training status when the page closes', async () => {
  const response = deferred<api.TrainingStatus>(); vi.mocked(api.trainingStatus).mockReturnValue(response.promise)
  await mount(); unmount()
  response.resolve(training); await settle(); await vi.advanceTimersByTimeAsync(10_000)
  expect(useLoraTrainingStore(pinia).training).toBeNull()
  expect(vi.mocked(api.trainingStatus).mock.calls[0]?.[0]?.aborted).toBe(true)
  expect(api.trainingStatus).toHaveBeenCalledOnce()
})

it('observes a submitted task whose start response arrives after re-entry', async () => {
  const response = deferred<api.AsyncTaskStarted>(); vi.mocked(api.startAutoLabel).mockReturnValue(response.promise)
  vi.mocked(api.autoLabelStatus).mockResolvedValue({ task_id: 'label-task', status: 'completed', current: 2, total: 2, progress: '' })
  await mount(); const store = useLoraTrainingStore(pinia), pending = store.startAutoLabel({})
  unmount(); await mount()
  response.resolve({ task_id: 'label-task', total: 2, message: '' }); await pending; await settle()
  expect(api.autoLabelStatus).toHaveBeenCalledWith('label-task', expect.any(AbortSignal))
  expect(store.autoLabelRunning).toBe(false)
  expect(api.startAutoLabel).toHaveBeenCalledOnce()
})

it('invalidates a pre-submission status when new training is accepted', async () => {
  const oldStatus = deferred<api.TrainingStatus>()
  vi.mocked(api.trainingStatus).mockReturnValueOnce(oldStatus.promise).mockResolvedValue(training)
  vi.mocked(api.startLoraTraining).mockResolvedValue({ message: 'Started', tensor_dir: 'tensors', output_dir: 'adapter', config: {} })
  await mount(); await useLoraTrainingStore(pinia).startTraining({ tensor_dir: 'tensors' }); await settle()
  oldStatus.resolve({ ...training, is_training: false, current_step: 0 }); await settle(); await vi.advanceTimersByTimeAsync(3000)
  expect(useLoraTrainingStore(pinia).isTraining).toBe(true)
  expect(vi.mocked(api.trainingStatus).mock.calls[0]?.[0]?.aborted).toBe(true)
})
