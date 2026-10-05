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

vi.mock('../../api/aceStepTraining', async original => ({ ...await original<typeof import('../../api/aceStepTraining')>(), trainingStatus: vi.fn(), autoLabelStatus: vi.fn(), preprocessStatus: vi.fn(), startAutoLabel: vi.fn(), startLoraTraining: vi.fn() }))
vi.mock('../../api/aceStep', async original => ({ ...await original<typeof import('../../api/aceStep')>(), health: vi.fn() }))
let app: App | undefined, pinia: Pinia
const training: api.TrainingStatus = { is_training: true, should_stop: false, current_step: 1, current_loss: 0.2, status: 'Running', config: { epochs: 3 }, tensor_dir: 'tensors', loss_history: [], tensorboard_url: null, tensorboard_logdir: null, training_log: '', start_time: 1, current_epoch: 1, steps_per_second: 1, estimated_time_remaining: 30, error: null }
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<T>(release => { resolve = release }); return { promise, resolve }
}
async function settle() { for (let i = 0; i < 12; i++) await nextTick() }
async function mount() { app = createApp(LoraTrainingPage).use(pinia).use(i18n); app.mount(document.body.appendChild(document.createElement('div'))); await settle() }
function unmount() { app?.unmount(); app = undefined; document.body.replaceChildren() }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); pinia = createPinia()
  const orchestrator = useOrchestratorStore(pinia)
  orchestrator.statuses.ace_step = { id: 'ace_step', label: 'ACE-Step', status: 'running', error: null }
  vi.mocked(ace.health).mockResolvedValue({ status: 'ok', service: 'ACE', version: '1', models_initialized: true, llm_initialized: true, loaded_model: 'model', loaded_lm_model: 'lm' })
  vi.mocked(api.trainingStatus).mockResolvedValue({ ...training, is_training: false })
})
afterEach(() => { unmount(); useLoraTrainingStore(pinia).stopBackgroundTasks(); vi.clearAllTimers(); vi.useRealTimers(); localStorage.clear() })

it('resumes observation of active training on re-entry without submitting new work', async () => {
  vi.mocked(api.trainingStatus).mockResolvedValue(training)
  await mount(); unmount()
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
