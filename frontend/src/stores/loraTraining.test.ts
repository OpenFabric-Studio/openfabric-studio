// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useLoraTrainingStore } from './loraTraining'
import * as api from '../api/aceStepTraining'
import { ApiError } from '../api/http'

vi.mock('../api/aceStepTraining', async original => ({ ...await original<typeof import('../api/aceStepTraining')>(),
  autoLabelStatus: vi.fn(), preprocessStatus: vi.fn(), latestAutoLabelStatus: vi.fn(), latestPreprocessStatus: vi.fn(),
  trainingStatus: vi.fn(), getSamples: vi.fn(), startAutoLabel: vi.fn(), startPreprocess: vi.fn(), startLoraTraining: vi.fn(), exportLora: vi.fn(),
}))
const idle = { task_id: null, status: 'idle', progress: '', current: 0, total: 0 } as const
const sample: api.DatasetSample = { index: 0, filename: 'voice.wav', audio_path: '/audio/voice.wav', duration: 10, caption: 'preserved', genre: '', prompt_override: null, lyrics: '', bpm: null, keyscale: '', timesignature: '', language: 'en', is_instrumental: false, labeled: true }
const label: api.AutoLabelStatus = { task_id: 'label', status: 'running', progress: 'Labeling', current: 2, total: 4, save_path: '/datasets/voice.json' }
const preprocess: api.PreprocessStatus = { task_id: 'preprocess', status: 'running', progress: 'Preparing', current: 2, total: 4 }
const training: api.TrainingStatus = { is_training: false, should_stop: false, current_step: 1, current_loss: 0.2, status: 'Idle', config: { epochs: 3 }, tensor_dir: '/tensors', loss_history: [], tensorboard_url: null, tensorboard_logdir: null, training_log: '', start_time: 1, current_epoch: 1, steps_per_second: 1, estimated_time_remaining: 30, error: null }
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>(release => { resolve = release }); return { promise, resolve }
}
async function settle() { await vi.advanceTimersByTimeAsync(0) }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); setActivePinia(createPinia())
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(idle)
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue(idle)
  vi.mocked(api.trainingStatus).mockResolvedValue(training)
  vi.mocked(api.getSamples).mockResolvedValue({ samples: [sample] })
})
afterEach(() => { useLoraTrainingStore().stopBackgroundTasks(); vi.clearAllTimers(); vi.useRealTimers() })

it('registers only the canonical adapter path returned by a successful safe export', async () => {
  const store = useLoraTrainingStore(), register = vi.fn()
  const adapter = 'C:\\exports\\voice\\adapter'
  vi.mocked(api.exportLora).mockResolvedValue({ export_path: 'C:\\exports\\voice', adapter_path: adapter, source: '/engine/lora_output/run/final', message: 'Exported' })
  await store.exportAndRegister('C:/exports/voice', 'lora_output/run', 'Voice', register)
  expect(register).toHaveBeenCalledWith('Voice', adapter)
  expect(store.lastExportedPath).toBe(adapter)
})
it('keeps the registry unchanged when a competing export creates the reviewed destination', async () => {
  const store = useLoraTrainingStore(), register = vi.fn()
  vi.mocked(api.exportLora).mockRejectedValue(new ApiError('lora_export_destination_exists', 409))
  await expect(store.exportAndRegister('/exports/voice', 'lora_output/run', 'Voice', register)).rejects.toThrow()
  expect(register).not.toHaveBeenCalled()
  expect(store.exportError).toContain('already exists')
  expect(store.lastExportedPath).toBe('')
})

it('explains a rejected tensor reuse without exposing an internal code or resubmitting', async () => {
  const store = useLoraTrainingStore()
  await store.startPreprocess({ output_dir: '/tensors', skip_existing: true }, async () => { throw new ApiError('preprocess_reuse_mismatch', 409) })
  expect(store.preprocessError).toContain('do not match')
  expect(store.preprocessSubmissionUnknown).toBe(false)
  expect(api.startPreprocess).not.toHaveBeenCalled()
})

it('keeps label progress and identity through repeated observation failures, then accepts completion', async () => {
  const store = useLoraTrainingStore()
  store.autoLabelTaskId = label.task_id; store.autoLabelRunning = true; store.autoLabelCurrent = 2; store.autoLabelTotal = 4
  vi.mocked(api.autoLabelStatus).mockRejectedValueOnce(new TypeError('network')).mockRejectedValueOnce(new TypeError('network'))
    .mockResolvedValue({ ...label, status: 'completed', current: 4, result: { message: '', labeled_count: 4, samples: [sample] } })
  store.startBackgroundTasks(); await settle()
  expect(store.autoLabelRunning).toBe(true)
  expect(store.autoLabelReconnecting).toBe(true)
  expect(store.autoLabelCurrent).toBe(2)
  expect(store.autoLabelTaskId).toBe('label')
  expect(store.autoLabelError).toBe('')
  await vi.advanceTimersByTimeAsync(4000)
  expect(store.autoLabelRunning).toBe(false)
  expect(store.autoLabelReconnecting).toBe(false)
  expect(store.samples).toEqual([sample])
  expect(api.startAutoLabel).not.toHaveBeenCalled()
})

it('retries preprocessing observation and keeps the original destination', async () => {
  const store = useLoraTrainingStore()
  store.preprocessTaskId = 'preprocess'; store.preprocessRunning = true; store.preprocessTaskOutputDir = '/tensors'
  vi.mocked(api.preprocessStatus).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ ...preprocess, status: 'completed', current: 4, result: { message: '', num_tensors: 4, output_dir: '/tensors' } })
  store.startBackgroundTasks(); await settle()
  expect(store.preprocessReconnecting).toBe(true)
  expect(store.preprocessRunning).toBe(true)
  expect(store.preprocessError).toBe('')
  await vi.advanceTimersByTimeAsync(2000)
  expect(store.preprocessOutputDir).toBe('/tensors')
  expect(store.preprocessReconnecting).toBe(false)
  expect(api.startPreprocess).not.toHaveBeenCalled()
})

it('retries failed initial training discovery before a running job is known', async () => {
  const store = useLoraTrainingStore()
  vi.mocked(api.trainingStatus).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ ...training, is_training: true, status: 'Training', current_step: 17 })
  store.startBackgroundTasks(); await settle()
  expect(store.trainingReconnecting).toBe(true)
  expect(store.nativeBusy).toBe(true)
  await vi.advanceTimersByTimeAsync(3000)
  expect(store.training?.current_step).toBe(17)
  expect(store.isTraining).toBe(true)
  expect(store.trainingReconnecting).toBe(false)
  expect(api.startLoraTraining).not.toHaveBeenCalled()
})

it('retries initial latest discovery and restores live native samples without loading a dataset', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  vi.mocked(api.latestAutoLabelStatus).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(label)
  vi.mocked(api.autoLabelStatus).mockResolvedValue(label)
  store.startBackgroundTasks(); await settle()
  expect(store.autoLabelReconnecting).toBe(true)
  await vi.advanceTimersByTimeAsync(2000)
  expect(store.autoLabelRunning).toBe(true)
  expect(store.autoLabelTaskId).toBe('label')
  expect(store.samples).toEqual([sample])
  expect(store.currentRecoveryContext.autoLabelTaskId).toBe('label')
  expect(api.getSamples).toHaveBeenCalledWith(expect.any(AbortSignal))
  expect(api.startAutoLabel).not.toHaveBeenCalled()
})

it('restores known preprocessing after reload using its saved task ID', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json', preprocessTaskId: 'preprocess', preprocessOutputDir: '/tensors' })
  vi.mocked(api.preprocessStatus).mockResolvedValue({ ...preprocess, status: 'completed', result: { message: '', num_tensors: 4, output_dir: '/tensors' } })
  store.startBackgroundTasks(); await settle()
  expect(store.preprocessOutputDir).toBe('/tensors')
  expect(store.preprocessRunning).toBe(false)
})

it('makes unmatched active work visible without replacing the selected dataset or tensors', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/selected.json' }); store.samples = [sample]
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(label)
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue(preprocess)
  store.startBackgroundTasks(); await settle()
  expect(store.discoveredAutoLabel?.task_id).toBe('label')
  expect(store.discoveredPreprocess?.task_id).toBe('preprocess')
  expect(store.nativeBusy).toBe(true)
  expect(store.autoLabelTaskId).toBe('')
  expect(store.preprocessTaskId).toBe('')
  expect(store.samples).toEqual([sample])
  expect(store.preprocessOutputDir).toBe('')
  expect(api.getSamples).not.toHaveBeenCalled()
})

it('does not adopt stale completed latest work without a saved matching task ID', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue({ ...label, status: 'completed', result: { message: '', labeled_count: 1, samples: [sample] } })
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue({ ...preprocess, status: 'completed', result: { message: '', num_tensors: 4, output_dir: '/old-tensors' } })
  store.startBackgroundTasks(); await settle()
  expect(store.samples).toEqual([])
  expect(store.autoLabelTaskId).toBe('')
  expect(store.preprocessOutputDir).toBe('')
  expect(store.nativeBusy).toBe(false)
})

it('continues discovery after a lost submission response and never repeats the submission', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  store.startBackgroundTasks(); await settle()
  vi.mocked(api.startAutoLabel).mockRejectedValueOnce(new TypeError('response lost'))
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(label)
  vi.mocked(api.autoLabelStatus).mockResolvedValue(label)
  await store.startAutoLabel({ only_unlabeled: true, save_path: '/datasets/voice.json' }); await settle()
  expect(store.autoLabelTaskId).toBe('label')
  expect(store.autoLabelSubmissionUnknown).toBe(false)
  expect(store.autoLabelRunning).toBe(true)
  expect(store.autoLabelError).toBe('')
  await vi.advanceTimersByTimeAsync(6000)
  expect(api.startAutoLabel).toHaveBeenCalledOnce()
})

it('marks a missing native task interrupted and continues latest discovery', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json', autoLabelTaskId: 'missing' })
  vi.mocked(api.autoLabelStatus).mockRejectedValue(new ApiError('Not found', 404))
  store.startBackgroundTasks(); await settle()
  expect(store.autoLabelInterrupted).toBe(true)
  expect(store.autoLabelReconnecting).toBe(false)
  expect(store.autoLabelRunning).toBe(false)
  expect(store.autoLabelTaskId).toBe('missing')
  expect(api.latestAutoLabelStatus).toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(6000)
  expect(api.autoLabelStatus).toHaveBeenCalledOnce()
})

it('keeps a native task failure separate from a reconnecting connection', async () => {
  const store = useLoraTrainingStore()
  store.autoLabelTaskId = 'label'; store.autoLabelRunning = true
  vi.mocked(api.autoLabelStatus).mockResolvedValue({ ...label, status: 'failed', error: 'Labeling failed' })
  store.startBackgroundTasks(); await settle(); await vi.advanceTimersByTimeAsync(6000)
  expect(store.autoLabelRunning).toBe(false)
  expect(store.autoLabelReconnecting).toBe(false)
  expect(store.autoLabelError).toBe('Labeling failed')
  expect(api.autoLabelStatus).toHaveBeenCalledOnce()
})

it('invalidates discovery and native sample responses after a dataset context switch', async () => {
  const store = useLoraTrainingStore(), response = deferred<{ samples: api.DatasetSample[] }>()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(label)
  vi.mocked(api.autoLabelStatus).mockResolvedValue(label)
  vi.mocked(api.getSamples).mockReturnValue(response.promise)
  store.startBackgroundTasks(); await settle()
  store.restoreRecoveryContext({ datasetPath: '/datasets/other.json' })
  response.resolve({ samples: [sample] }); await settle()
  expect(store.samples).toEqual([])
  expect(store.autoLabelTaskId).toBe('')
})

it('aborts discovery and ignores accepted late task status after teardown', async () => {
  const response = deferred<api.LatestAutoLabelStatus>(), store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  vi.mocked(api.latestAutoLabelStatus).mockReturnValue(response.promise)
  store.startBackgroundTasks(); await settle(); store.stopBackgroundTasks()
  response.resolve(label); await settle(); await vi.advanceTimersByTimeAsync(10_000)
  expect(store.autoLabelTaskId).toBe('')
  expect(store.samples).toEqual([])
  expect(vi.mocked(api.latestAutoLabelStatus).mock.calls[0]?.[0]?.aborted).toBe(true)
  expect(api.latestAutoLabelStatus).toHaveBeenCalledOnce()
})

it('clears discovered busy status when recovered labeling completes', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(label)
  vi.mocked(api.autoLabelStatus).mockResolvedValueOnce(label).mockResolvedValue({ ...label, status: 'completed', result: { message: '', labeled_count: 4, samples: [sample] } })
  await store.startBackgroundTasks()
  expect(store.nativeBusy).toBe(true)
  await vi.advanceTimersByTimeAsync(2000)
  expect(store.nativeBusy).toBe(false)
})

it('retries live sample restoration and resolves initial observation only after samples arrive', async () => {
  const response = deferred<{ samples: api.DatasetSample[] }>(), store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValue(label)
  vi.mocked(api.autoLabelStatus).mockResolvedValue(label)
  vi.mocked(api.getSamples).mockRejectedValueOnce(new TypeError('offline')).mockReturnValue(response.promise)
  await store.startBackgroundTasks()
  expect(store.autoLabelReconnecting).toBe(true)
  await vi.advanceTimersByTimeAsync(2000)
  response.resolve({ samples: [sample] }); await settle()
  expect(store.autoLabelReconnecting).toBe(false)
  expect(store.samples).toEqual([sample])
})

it('does not let an accepted label response attach to a different dataset', async () => {
  const response = deferred<api.AsyncTaskStarted>(), store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  vi.mocked(api.startAutoLabel).mockReturnValue(response.promise)
  const pending = store.startAutoLabel({ save_path: '/datasets/voice.json' })
  store.restoreRecoveryContext({ datasetPath: '/datasets/other.json' })
  response.resolve({ task_id: 'old-dataset-task', total: 4, message: '' }); await pending
  expect(store.autoLabelTaskId).toBe('')
  expect(store.autoLabelStarting).toBe(false)
  expect(store.autoLabelRunning).toBe(false)
})

it('keeps unknown preprocessing submissions visible without adopting unrelated latest output', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json' })
  await store.startBackgroundTasks()
  vi.mocked(api.startPreprocess).mockRejectedValue(new TypeError('response lost'))
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue(preprocess)
  await store.startPreprocess({ output_dir: '/new-tensors' }); await settle()
  expect(store.preprocessSubmissionUnknown).toBe(true)
  expect(store.nativeBusy).toBe(true)
  expect(store.preprocessTaskId).toBe('')
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue({ ...preprocess, status: 'completed', result: { message: '', output_dir: '/old-tensors', num_tensors: 4 } })
  await vi.advanceTimersByTimeAsync(2000)
  expect(store.preprocessOutputDir).toBe('')
  expect(store.preprocessInterrupted).toBe(true)
  expect(store.preprocessSubmissionUnknown).toBe(false)
  expect(api.startPreprocess).toHaveBeenCalledOnce()
})

it('rejects a completed known preprocessing task with a different output path', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json', preprocessTaskId: 'preprocess', preprocessOutputDir: '/expected-tensors' })
  vi.mocked(api.preprocessStatus).mockResolvedValue({ ...preprocess, status: 'completed', result: { message: '', output_dir: '/unrelated-tensors', num_tensors: 4 } })
  await store.startBackgroundTasks()
  expect(store.preprocessOutputDir).toBe('')
  expect(store.preprocessInterrupted).toBe(true)
})

it('does not attach unrelated native training to the selected tensor directory', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json', trainingTensorDir: '/my-tensors' })
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, is_training: true })
  await store.startBackgroundTasks()
  expect(store.training).toBeNull()
  expect(store.discoveredTraining?.is_training).toBe(true)
  expect(store.trainingInterrupted).toBe(true)
  expect(store.nativeBusy).toBe(true)
})

it('recovers an accepted training submission after its acknowledgement is lost', async () => {
  const store = useLoraTrainingStore()
  await store.startBackgroundTasks()
  vi.mocked(api.startLoraTraining).mockRejectedValue(new TypeError('response lost'))
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, is_training: true, current_step: 29 })
  await store.startTraining({ tensor_dir: '/tensors' }); await settle()
  expect(store.training?.current_step).toBe(29)
  expect(store.trainingSubmissionUnknown).toBe(false)
  expect(store.trainingReconnecting).toBe(false)
  expect(api.startLoraTraining).toHaveBeenCalledOnce()
})

it('accepts a preprocessing submission wrapper while keeping observer ownership', async () => {
  const store = useLoraTrainingStore()
  const submit = vi.fn().mockResolvedValue({ task_id: 'preprocess', total: 4, message: '' })
  vi.mocked(api.preprocessStatus).mockResolvedValue(preprocess)
  await store.startBackgroundTasks()
  await store.startPreprocess({ output_dir: '/tensors', skip_existing: true }, submit); await settle()
  expect(submit).toHaveBeenCalledWith({ output_dir: '/tensors', skip_existing: true })
  expect(store.preprocessTaskId).toBe('preprocess')
  expect(api.startPreprocess).not.toHaveBeenCalled()
})

it('does not spin on an expired per-ID endpoint when latest still reports the same task', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json', autoLabelTaskId: 'label' })
  vi.mocked(api.autoLabelStatus).mockRejectedValue(new ApiError('Task not found', 404))
  vi.mocked(api.latestAutoLabelStatus).mockResolvedValueOnce(label).mockResolvedValue(idle)
  store.startBackgroundTasks(); await settle()
  await vi.advanceTimersByTimeAsync(6000)
  expect(store.autoLabelInterrupted).toBe(true)
  expect(api.autoLabelStatus).toHaveBeenCalledOnce()
  expect(api.latestAutoLabelStatus).toHaveBeenCalledTimes(2)
})

it('marks an unknown training submission interrupted when native work belongs to different tensors', async () => {
  const store = useLoraTrainingStore()
  await store.startBackgroundTasks()
  vi.mocked(api.startLoraTraining).mockRejectedValue(new TypeError('response lost'))
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, tensor_dir: '/other-tensors', is_training: true })
  await store.startTraining({ tensor_dir: '/tensors' }); await settle()
  expect(store.trainingSubmissionUnknown).toBe(false)
  expect(store.trainingInterrupted).toBe(true)
  expect(store.training).toMatchObject({ is_training: false })
})

it('waits for latest discovery after a missing saved task before declaring native work idle', async () => {
  const store = useLoraTrainingStore(), response = deferred<api.LatestAutoLabelStatus>()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json', autoLabelTaskId: 'missing' })
  vi.mocked(api.autoLabelStatus).mockRejectedValue(new ApiError('Task not found', 404))
  vi.mocked(api.latestAutoLabelStatus).mockReturnValue(response.promise)
  let initialized = false
  const observation = store.startBackgroundTasks().then(() => { initialized = true })
  await settle()
  expect(initialized).toBe(false)
  response.resolve({ ...label, task_id: 'other-label', save_path: '/other.json' }); await observation
  expect(store.nativeBusy).toBe(true)
})

it('keeps initial discovery busy until the native idle responses arrive', async () => {
  const response = deferred<api.LatestAutoLabelStatus>(), store = useLoraTrainingStore()
  vi.mocked(api.latestAutoLabelStatus).mockReturnValue(response.promise)
  const observation = store.startBackgroundTasks(); await settle()
  expect(store.nativeBusy).toBe(true)
  response.resolve(idle); await observation
  expect(store.nativeBusy).toBe(false)
})

it('accepts a labeling submission wrapper while preserving remaining-only settings and task observation', async () => {
  const store = useLoraTrainingStore()
  const submit = vi.fn().mockResolvedValue({ task_id: 'label', total: 4, message: '' })
  vi.mocked(api.autoLabelStatus).mockResolvedValue(label)
  await store.startBackgroundTasks()
  await store.startAutoLabel({ only_unlabeled: true, save_path: '/datasets/voice.json' }, submit); await settle()
  expect(submit).toHaveBeenCalledWith({ only_unlabeled: true, save_path: '/datasets/voice.json' })
  expect(store.autoLabelTaskId).toBe('label')
  expect(store.autoLabelRunning).toBe(true)
  expect(api.startAutoLabel).not.toHaveBeenCalled()
})

it('restores native samples for known live preprocessing without resetting the builder', async () => {
  const store = useLoraTrainingStore()
  store.restoreRecoveryContext({ datasetPath: '/datasets/voice.json', preprocessTaskId: 'preprocess', preprocessOutputDir: '/tensors' })
  vi.mocked(api.preprocessStatus).mockResolvedValue(preprocess)
  await store.startBackgroundTasks()
  expect(store.samples).toEqual([sample])
  expect(api.getSamples).toHaveBeenCalledWith(expect.any(AbortSignal))
})

it('clears the discovered preprocessing busy status after a retained completed task is observed', async () => {
  const store = useLoraTrainingStore()
  store.datasetPath = '/datasets/voice.json'; store.preprocessTaskId = 'preprocess'; store.preprocessTaskOutputDir = '/tensors'
  vi.mocked(api.latestPreprocessStatus).mockResolvedValue(preprocess)
  vi.mocked(api.preprocessStatus).mockResolvedValueOnce(preprocess).mockResolvedValue({ ...preprocess, status: 'completed', result: { message: '', num_tensors: 4, output_dir: '/tensors' } })
  await store.startBackgroundTasks()
  await vi.advanceTimersByTimeAsync(2000)
  expect(store.preprocessOutputDir).toBe('/tensors')
  expect(store.nativeBusy).toBe(false)
})

it('preserves confirmed training progress and marks interruption when the engine forgets its run', async () => {
  const store = useLoraTrainingStore()
  vi.mocked(api.trainingStatus).mockResolvedValueOnce({ ...training, is_training: true, current_step: 29, status: 'Training' })
    .mockResolvedValue({ ...training, is_training: false, tensor_dir: '', start_time: null, current_step: 0, current_epoch: 0, status: 'Idle' })
  await store.startBackgroundTasks()
  await vi.advanceTimersByTimeAsync(3000)
  expect(store.training?.current_step).toBe(29)
  expect(store.isTraining).toBe(false)
  expect(store.trainingInterrupted).toBe(true)
  expect(store.nativeBusy).toBe(false)
})

it('retains interrupted training counters when observation restarts for the same selected run', async () => {
  const store = useLoraTrainingStore()
  vi.mocked(api.trainingStatus).mockResolvedValueOnce({ ...training, is_training: true, current_step: 29 })
    .mockResolvedValue({ ...training, is_training: false, tensor_dir: '', start_time: null, current_step: 0, current_epoch: 0, status: 'Idle' })
  await store.startBackgroundTasks(); await vi.advanceTimersByTimeAsync(3000)
  store.stopBackgroundTasks(); await store.startBackgroundTasks()
  expect(store.training?.current_step).toBe(29)
  expect(store.trainingInterrupted).toBe(true)
  expect(store.isTraining).toBe(false)
})

it('does not keep a stop control attached to an unrelated replacement training job', async () => {
  const store = useLoraTrainingStore()
  store.trainingTensorDir = '/tensors'
  vi.mocked(api.trainingStatus).mockResolvedValueOnce({ ...training, is_training: true, current_step: 29 })
    .mockResolvedValue({ ...training, is_training: true, tensor_dir: '/other-tensors', current_step: 3 })
  await store.startBackgroundTasks(); await vi.advanceTimersByTimeAsync(3000)
  expect(store.isTraining).toBe(false)
  expect(store.training?.current_step).toBe(29)
  expect(store.trainingInterrupted).toBe(true)
  expect(store.discoveredTraining?.tensor_dir).toBe('/other-tensors')
  expect(store.nativeBusy).toBe(true)
})

it('records the tensor identity discovered for an already running training job', async () => {
  const store = useLoraTrainingStore()
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, is_training: true })
  await store.startBackgroundTasks()
  expect(store.currentRecoveryContext.trainingTensorDir).toBe('/tensors')
})
