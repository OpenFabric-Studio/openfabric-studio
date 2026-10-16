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

vi.mock('../../api/aceStepTraining', async original => ({ ...await original<typeof import('../../api/aceStepTraining')>(), trainingStatus: vi.fn(), autoLabelStatus: vi.fn(), preprocessStatus: vi.fn(), latestAutoLabelStatus: vi.fn(), latestPreprocessStatus: vi.fn(), getSamples: vi.fn(), startAutoLabel: vi.fn(), startLoraTraining: vi.fn(), loraExportOptions: vi.fn(), pickLoraExportFolder: vi.fn(), previewLoraExport: vi.fn(), exportLora: vi.fn(), checkTrainedLoraFiles: vi.fn() }))
vi.mock('../../api/aceStep', async original => ({ ...await original<typeof import('../../api/aceStep')>(), health: vi.fn(), initModel: vi.fn() }))
vi.mock('../../api/loraDataset', () => ({ listLoraDatasets: vi.fn(), getLoraWorkspace: vi.fn(), saveLoraWorkspace: vi.fn(), createLoraDataset: vi.fn(), renameLoraDataset: vi.fn(), trashLoraDataset: vi.fn(), restoreLoraDataset: vi.fn(), removeLoraDatasetRecords: vi.fn(), loadLoraDataset: vi.fn(), scanLoraDataset: vi.fn(), saveLoraDataset: vi.fn(), preprocessLoraDataset: vi.fn(), autoLabelLoraDataset: vi.fn(), updateLoraDatasetSample: vi.fn(), uploadDatasetFiles: vi.fn() }))
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
  vi.useFakeTimers(); vi.resetAllMocks(); localStorage.clear(); pinia = createPinia()
  const orchestrator = useOrchestratorStore(pinia)
  orchestrator.statuses.ace_step = { id: 'ace_step', label: 'ACE-Step', status: 'running', error: null }
  vi.mocked(ace.health).mockResolvedValue({ status: 'ok', service: 'ACE', version: '1', models_initialized: true, llm_initialized: true, loaded_model: 'model', loaded_lm_model: 'lm' })
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, is_training: false })
  const idle = { task_id: null, status: 'idle', progress: '', current: 0, total: 0 } as const
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(idle)
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue(idle)
  vi.mocked(api.getSamples).mockResolvedValue({ samples: [] })
  vi.mocked(api.loraExportOptions).mockResolvedValue({ can_pick: true })
  vi.mocked(api.pickLoraExportFolder).mockResolvedValue({ path: '/exports' })
  vi.mocked(api.checkTrainedLoraFiles).mockResolvedValue({ checkpoint: null })
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [], issues: [] })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 0, selected_dataset_id: null, form: {} })
  vi.mocked(datasets.saveLoraWorkspace).mockImplementation(async workspace => ({ ...workspace, revision: (workspace.revision ?? 0) + 1 }))
})

const savedDataset = { id: 'a'.repeat(32), name: 'Saved voice', dataset_path: 'datasets/voice/dataset.json', audio_dir: 'datasets/voice', sample_count: 22, labeled_count: 22, revision: 1, trashed: false, available: true, error_code: '' }
const sample = { id: 'sample-one', index: 0, filename: 'voice.wav', audio_path: 'datasets/voice/voice.wav', duration: 10, caption: 'Original', genre: '', prompt_override: null, lyrics: '', bpm: null, keyscale: '', timesignature: '', language: 'en', is_instrumental: false, labeled: true }
it('shows dataset issue details and clears only confirmed trash records while preserving the selected dataset', async () => {
  const trashed = { ...savedDataset, id: 'b'.repeat(32), name: 'Old dataset', trashed: true }
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [savedDataset, trashed], issues: [{ path: 'datasets/broken.json', code: 'dataset_invalid' }] })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, selected_dataset_id: savedDataset.id, form: {} })
  vi.mocked(datasets.loadLoraDataset).mockResolvedValue({ dataset: savedDataset, samples: [sample] })
  vi.mocked(datasets.removeLoraDatasetRecords).mockResolvedValue({ removed_ids: [trashed.id] })
  await mount()
  expect(document.body.textContent).toContain('datasets/broken.json')
  expect(document.body.textContent).toContain(i18n.global.t('loraRecovery.issueInvalid'))
  button('Remove record').click(); await settle()
  expect(datasets.removeLoraDatasetRecords).not.toHaveBeenCalled()
  button('Remove records, keep files').click(); await settle()
  expect(datasets.removeLoraDatasetRecords).toHaveBeenCalledWith([{ id: trashed.id, revision: trashed.revision }], expect.any(AbortSignal))
  expect(document.body.textContent).not.toContain('Old dataset')
  expect(document.querySelector('select[aria-label="Saved dataset"]')).toHaveProperty('value', savedDataset.id)
  expect(useLoraTrainingStore(pinia).samples[0]?.caption).toBe(sample.caption)
})
it('keeps training available when only the automatic labeling model is unloaded', async () => {
  vi.mocked(ace.health).mockResolvedValue({ status: 'ok', service: 'ACE', version: '1', models_initialized: true, llm_initialized: false, loaded_model: 'model', loaded_lm_model: null })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, training_tensor_dir: 'datasets/voice/tensors', form: { tensor_dir: 'datasets/voice/tensors' } })
  await mount()
  expect(document.body.textContent).toContain('Your saved labels can be used for preprocessing and training.')
  expect(button('Start training').disabled).toBe(false)
})
it('explains that preprocessing and training need the base model when it is unloaded', async () => {
  vi.mocked(ace.health).mockResolvedValue({ status: 'ok', service: 'ACE', version: '1', models_initialized: false, llm_initialized: false, loaded_model: null, loaded_lm_model: null })
  await mount()
  expect(document.body.textContent).toContain('Load the ACE-Step model before preprocessing or training.')
})
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
it('explains the first-update wait and hides placeholder loss and empty TensorBoard', async () => {
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, current_step: 0, current_epoch: 0, current_loss: 0, loss_history: [], status: 'Training parameters', tensorboard_url: 'http://127.0.0.1:6006' })
  await mount()
  expect(document.body.textContent).toContain('Waiting for the first completed training step')
  expect(document.body.textContent).not.toContain('loss 0.0000')
  expect(document.querySelector('a[href="http://127.0.0.1:6006"]')).toBeNull()
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, current_step: 1, current_loss: 0.25, tensorboard_url: 'http://127.0.0.1:6006' })
  await vi.advanceTimersByTimeAsync(3000); await settle()
  expect(document.body.textContent).not.toContain('Waiting for the first completed training step')
  expect(document.body.textContent).toContain('0.2500')
  expect(document.querySelector('a[href="http://127.0.0.1:6006"]')).not.toBeNull()
})
const exportResult = { message: 'Exported', export_path: '/exports/New voice', adapter_path: '/exports/New voice/adapter', source: '/engine/lora_output/run/final' }
function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!found) throw new Error(`Missing button ${label}`)
  return found
}
const savedCheckpoint = { checkpoint: { source: '/engine/lora_output/run/final', adapter_path: '/engine/lora_output/run/final/adapter', kind: 'final' as const } }
async function readyCheck() {
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, form: { lora_output_dir: 'lora_output/run' } })
  await mount()
}
it('checks for a saved final adapter without starting training or exporting', async () => {
  vi.mocked(api.checkTrainedLoraFiles).mockResolvedValue(savedCheckpoint)
  await readyCheck()
  expect(api.checkTrainedLoraFiles).not.toHaveBeenCalled()
  expect([...document.querySelectorAll('button')].filter(item => item.textContent?.trim() === 'Check for trained files').every(item => !item.closest('label'))).toBe(true)
  button('Check for trained files').click(); await settle()
  expect(api.checkTrainedLoraFiles).toHaveBeenCalledWith({ lora_output_dir: 'lora_output/run' }, expect.any(AbortSignal))
  expect(document.querySelector('[data-trained-files-result]')?.textContent).toContain('Final adapter found')
  expect(document.querySelector('[data-trained-files-result]')?.textContent).toContain(savedCheckpoint.checkpoint.source)
  expect(document.querySelector('a[href="#lora-export"]')?.textContent).toContain('Go to export')
  expect(document.querySelector<HTMLInputElement>('[name="lora-export-source"]')?.value).toBe('lora_output/run')
  expect(api.startLoraTraining).not.toHaveBeenCalled()
  expect(api.previewLoraExport).not.toHaveBeenCalled()
  expect(api.exportLora).not.toHaveBeenCalled()
})
it('distinguishes a saved checkpoint from a final adapter and an empty training folder', async () => {
  await readyCheck()
  button('Check for trained files').click(); await settle()
  expect(document.querySelector('[data-trained-files-result]')?.textContent).toContain('No saved adapter found')
  expect(document.querySelector('a[href="#lora-export"]')).toBeNull()
  vi.mocked(api.checkTrainedLoraFiles).mockResolvedValue({ checkpoint: { source: '/engine/run/checkpoints/epoch_5_loss_0.25', adapter_path: '/engine/run/checkpoints/epoch_5_loss_0.25/adapter', kind: 'checkpoint' } })
  button('Check for trained files').click(); await settle()
  expect(document.querySelector('[data-trained-files-result]')?.textContent).toContain('Saved checkpoint found')
  expect(document.querySelector('[data-trained-files-result]')?.textContent).toContain('epoch_5_loss_0.25')
})
it('checks saved files while the model is stopped without initializing it', async () => {
  useOrchestratorStore(pinia).statuses.ace_step = { id: 'ace_step', label: 'ACE-Step', status: 'stopped', error: null }
  vi.mocked(api.checkTrainedLoraFiles).mockResolvedValue(savedCheckpoint)
  await readyCheck()
  button('Check for trained files').click(); await settle()
  expect(document.querySelector('[data-trained-files-result]')?.textContent).toContain('Final adapter found')
  expect(ace.initModel).not.toHaveBeenCalled()
  expect(api.startLoraTraining).not.toHaveBeenCalled()
})
it('shows a safe check failure without presenting it as a missing adapter', async () => {
  vi.mocked(api.checkTrainedLoraFiles).mockRejectedValue(new Error('private path and internal failure'))
  await readyCheck()
  button('Check for trained files').click(); await settle()
  expect(document.querySelector('[data-trained-files-error]')?.textContent).toContain('Could not check this training run')
  expect(document.body.textContent).not.toContain('private path')
  expect(document.body.textContent).not.toContain('No saved adapter found')
})
it('cancels and ignores old checks when the training folder changes', async () => {
  const response = deferred<typeof savedCheckpoint>()
  vi.mocked(api.checkTrainedLoraFiles).mockReturnValueOnce(response.promise)
  await readyCheck()
  button('Check for trained files').click(); await settle()
  const field = document.querySelector('[name="lora-export-source"]')
  if (!(field instanceof HTMLInputElement)) throw new Error('Missing training run field')
  field.value = 'lora_output/new-run'; field.dispatchEvent(new Event('input')); await settle()
  expect(vi.mocked(api.checkTrainedLoraFiles).mock.calls[0]?.[1]?.aborted).toBe(true)
  vi.mocked(api.checkTrainedLoraFiles).mockResolvedValue({ checkpoint: null })
  button('Check for trained files').click(); await settle()
  response.resolve(savedCheckpoint); await settle()
  expect(document.querySelector('[data-trained-files-result]')?.textContent).toContain('No saved adapter found')
  expect(document.body.textContent).not.toContain(savedCheckpoint.checkpoint.source)
})
it('clears a saved-file result after editing the training folder', async () => {
  vi.mocked(api.checkTrainedLoraFiles).mockResolvedValue(savedCheckpoint)
  await readyCheck()
  button('Check for trained files').click(); await settle()
  const field = document.querySelector('[name="lora-export-source"]')
  if (!(field instanceof HTMLInputElement)) throw new Error('Missing training run field')
  field.value = 'lora_output/new-run'; field.dispatchEvent(new Event('input')); await settle()
  expect(document.querySelector('[data-trained-files-result]')).toBeNull()
})
it('cancels pending checks when switching datasets even with the same training folder', async () => {
  const other = { ...savedDataset, id: 'b'.repeat(32), name: 'Other voice' }
  vi.mocked(datasets.listLoraDatasets).mockResolvedValue({ datasets: [savedDataset, other] })
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, selected_dataset_id: savedDataset.id, form: { lora_output_dir: 'lora_output/run' } })
  vi.mocked(datasets.loadLoraDataset).mockImplementation(async id => ({ dataset: id === other.id ? other : savedDataset, samples: [] }))
  const response = deferred<typeof savedCheckpoint>()
  vi.mocked(api.checkTrainedLoraFiles).mockReturnValue(response.promise)
  await mount()
  button('Check for trained files').click(); await settle()
  const select = document.querySelector('select[aria-label="Saved dataset"]')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing dataset selection')
  select.value = other.id; select.dispatchEvent(new Event('change')); await settle()
  response.resolve(savedCheckpoint); await settle()
  expect(vi.mocked(api.checkTrainedLoraFiles).mock.calls[0]?.[1]?.aborted).toBe(true)
  expect(document.querySelector('[data-trained-files-result]')).toBeNull()
})
it('aborts a pending saved-file check when leaving the page', async () => {
  const response = deferred<typeof savedCheckpoint>()
  vi.mocked(api.checkTrainedLoraFiles).mockReturnValue(response.promise)
  await readyCheck()
  button('Check for trained files').click(); await settle(); unmount()
  response.resolve(savedCheckpoint); await settle()
  expect(vi.mocked(api.checkTrainedLoraFiles).mock.calls[0]?.[1]?.aborted).toBe(true)
  expect(api.startLoraTraining).not.toHaveBeenCalled()
  expect(api.exportLora).not.toHaveBeenCalled()
})
async function readyExport() {
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, form: { lora_output_dir: 'lora_output/run', registry_name: 'New voice', export_path: '/old/exact-destination' } })
  vi.mocked(api.previewLoraExport).mockResolvedValue(exportResult)
  vi.mocked(api.exportLora).mockResolvedValue(exportResult)
  await mount()
  button('Choose folder').click(); await settle()
}
it('checks and exports the same exact training folder text', async () => {
  await readyExport()
  const field = document.querySelector('[name="lora-export-source"]')
  if (!(field instanceof HTMLInputElement)) throw new Error('Missing training run field')
  const folder = 'lora_output/run with spaces '
  field.value = folder; field.dispatchEvent(new Event('input')); await settle()
  button('Check for trained files').click(); await settle()
  button('Export and register').click(); await settle()
  expect(api.checkTrainedLoraFiles).toHaveBeenCalledWith({ lora_output_dir: folder }, expect.any(AbortSignal))
  expect(api.previewLoraExport).toHaveBeenCalledWith({ export_path: '/exports/New voice', lora_output_dir: folder }, expect.any(AbortSignal))
})
it('chooses a parent folder and reviews the new destination before exporting or registering', async () => {
  await readyExport()
  expect(document.querySelector<HTMLInputElement>('[name="lora-export-parent"]')?.value).toBe('/exports')
  button('Export and register').click(); await settle()
  expect(api.previewLoraExport).toHaveBeenCalledWith({ export_path: '/exports/New voice', lora_output_dir: 'lora_output/run' }, expect.any(AbortSignal))
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain('/exports/New voice')
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain('No existing files will be deleted or replaced')
  expect(api.exportLora).not.toHaveBeenCalled()
  button('Create folder and export').click(); await settle()
  expect(api.exportLora).toHaveBeenCalledWith({ export_path: '/exports/New voice', lora_output_dir: 'lora_output/run' })
  expect(localStorage.getItem('aicollector_ace_loras_v1')).toContain('/exports/New voice/adapter')
})
it('does not reinterpret the old export path as a parent or export after cancelling review', async () => {
  await readyExport()
  expect(document.body.textContent).not.toContain('/old/exact-destination')
  button('Export and register').click(); await settle()
  button('Cancel').click(); await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
  expect(api.exportLora).not.toHaveBeenCalled()
  expect(localStorage.getItem('aicollector_ace_loras_v1')).toBeNull()
})
it('refuses an existing destination without registering an adapter', async () => {
  await readyExport()
  vi.mocked(api.previewLoraExport).mockRejectedValue(new ApiError('lora_export_destination_exists', 409))
  button('Export and register').click(); await settle()
  expect(document.body.textContent).toContain('already exists')
  expect(api.exportLora).not.toHaveBeenCalled()
  expect(localStorage.getItem('aicollector_ace_loras_v1')).toBeNull()
})
it('ignores a folder picker result after leaving the page', async () => {
  const picked = deferred<{ path: string | null }>()
  vi.mocked(api.pickLoraExportFolder).mockReturnValue(picked.promise)
  await mount()
  button('Choose folder').click(); await settle(); unmount()
  const saves = vi.mocked(datasets.saveLoraWorkspace).mock.calls.length
  picked.resolve({ path: '/late-folder' }); await settle()
  expect(vi.mocked(datasets.saveLoraWorkspace).mock.calls).toHaveLength(saves)
  expect(api.previewLoraExport).not.toHaveBeenCalled()
})
it('can review a saved training run without loading the model or a dataset', async () => {
  const orchestrator = useOrchestratorStore(pinia)
  orchestrator.statuses.ace_step = { id: 'ace_step', label: 'ACE-Step', status: 'stopped', error: null }
  vi.mocked(datasets.getLoraWorkspace).mockResolvedValue({ revision: 1, form: { lora_output_dir: 'lora_output/run', export_parent_dir: '/exports', registry_name: 'New voice' } })
  vi.mocked(api.previewLoraExport).mockResolvedValue(exportResult)
  await mount()
  button('Export and register').click(); await settle()
  expect(api.previewLoraExport).toHaveBeenCalledWith({ export_path: '/exports/New voice', lora_output_dir: 'lora_output/run' }, expect.any(AbortSignal))
  expect(ace.initModel).not.toHaveBeenCalled()
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

it('labels running preprocessing as attempted samples and retains it while reconnecting', async () => {
  const store = useLoraTrainingStore(pinia)
  store.samples = [sample]; store.preprocessRunning = true; store.preprocessTaskId = 'prepare-task'
  vi.mocked(api.preprocessStatus).mockResolvedValueOnce({ task_id: 'prepare-task', status: 'running', current: 1, total: 2, progress: '' }).mockRejectedValue(new Error('offline'))
  await mount()
  expect(document.body.textContent).toContain('1 / 2 samples attempted')
  expect(document.body.textContent).not.toContain('1 / 2 tensors')
  await vi.advanceTimersByTimeAsync(2000); await settle()
  expect(document.body.textContent).toContain('1 / 2 samples attempted')
  expect(document.body.textContent).toContain('Reconnecting')
})

it('shows zero tensors and a clear failure instead of a full success bar after all attempts fail', async () => {
  const store = useLoraTrainingStore(pinia)
  store.samples = [sample]; store.preprocessRunning = true; store.preprocessTaskId = 'prepare-task'
  vi.mocked(api.preprocessStatus).mockResolvedValue({ task_id: 'prepare-task', status: 'completed', current: 1, total: 1, progress: '',
    result: { output_dir: 'completed/tensors', num_tensors: 0, message: '✅ Preprocessed 0/1 samples (1 failed)' } })
  await mount()
  const section = [...document.querySelectorAll('section')].find(item => item.querySelector('h2')?.textContent === i18n.global.t('lora.step4Title'))
  expect(section?.textContent).toContain('0 / 1 tensors ready')
  expect(section?.textContent).toContain('No training tensors were created')
  expect(section?.textContent).not.toContain('✅')
  expect(section?.querySelector('[style*="width: 100%"]')).toBeNull()
  expect(button(i18n.global.t('lora.startTraining')).disabled).toBe(true)
})

it('shows only confirmed ready tensors after partial preprocessing', async () => {
  const store = useLoraTrainingStore(pinia)
  store.samples = [sample]; store.preprocessRunning = true; store.preprocessTaskId = 'prepare-task'
  vi.mocked(api.preprocessStatus).mockResolvedValue({ task_id: 'prepare-task', status: 'completed', current: 2, total: 2, progress: '',
    result: { output_dir: 'completed/tensors', num_tensors: 1, message: '✅ Preprocessed 1/2 samples (1 failed)' } })
  await mount()
  expect(document.body.textContent).toContain('1 / 2 tensors ready')
  expect(document.body.textContent).not.toContain('2 / 2 tensors')
  expect(document.body.textContent).toContain('Some samples did not produce training tensors')
  expect(document.body.textContent).not.toContain('✅')
  expect(button(i18n.global.t('lora.startTraining')).disabled).toBe(false)
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
