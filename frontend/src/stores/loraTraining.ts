import { acceptHMRUpdate, defineStore } from 'pinia'
import * as api from '../api/aceStepTraining'
import { i18n } from '../i18n'
import { ApiError } from '../api/http'
import { createPollingLoop, type PollContext, type PollingLoop } from '../composables/polling'

const t = i18n.global.t

export function loraExportErrorText(cause: unknown): string {
  if (cause instanceof ApiError) {
    switch (cause.message) {
      case 'lora_export_destination_exists': return t('loraExport.errors.exists')
      case 'lora_export_destination_unsafe': return t('loraExport.errors.destination')
      case 'lora_export_source_unsafe':
      case 'lora_export_source_missing':
      case 'lora_export_adapter_missing': return t('loraExport.errors.source')
      case 'lora_export_copy_failed': return t('loraExport.errors.copy')
      case 'lora_export_picker_unavailable':
      case 'lora_export_picker_failed': return t('loraExport.errors.picker')
      case 'dataset_busy':
      case 'dataset_native_unavailable':
      case 'music_queue_busy': return t('loraExport.errors.busy')
    }
  }
  return t('loraExport.errors.failed')
}

function errorText(cause: unknown): string {
  if (cause instanceof ApiError) {
    switch (cause.message) {
      case 'dataset_sources_changed': return t('loraRecovery.sourcesChanged')
      case 'preprocess_reuse_mismatch':
      case 'preprocess_output_exists':
      case 'preprocess_model_unverified': return t('loraRecovery.reuseMismatch')
      case 'dataset_revision_conflict':
      case 'workspace_revision_conflict': return t('loraRecovery.conflict')
      case 'dataset_busy': return t('loraRecovery.busy')
      case 'dataset_path_unsafe': return t('loraRecovery.unsafe')
      case 'training_output_exists': return t('loraRecovery.trainingOutputExists')
    }
  }
  return cause instanceof ApiError && cause.message === 'native_operation_failed'
    ? t('storeErrors.nativeOperationFailed') : cause instanceof Error ? cause.message : String(cause)
}

const AUTOLABEL_POLL_MS = 2000
const PREPROCESS_POLL_MS = 2000
const TRAINING_POLL_MS = 3000
export interface LoraRecoveryContext {
  datasetPath: string
  autoLabelTaskId?: string
  preprocessTaskId?: string
  preprocessOutputDir?: string
  trainingTensorDir?: string
}

function samePath(left: string, right: string): boolean {
  const normalize = (value: string) => value.replace(/\\/g, '/').replace(/^(\.\/)+/, '').replace(/\/+$/, '')
  return !!left && !!right && normalize(left) === normalize(right)
}

function submissionRejected(cause: unknown): boolean {
  return cause instanceof ApiError && cause.status >= 400 && cause.status < 500
    || cause instanceof ApiError && cause.message === 'native_operation_failed'
}

function observedLoop(task: (context: PollContext) => Promise<boolean>, delay: number): { loop: PollingLoop; initial: Promise<void> } {
  let complete = () => {}
  const initial = new Promise<void>(resolve => { complete = resolve })
  const loop = createPollingLoop(async context => {
    try { return await task(context) } finally { complete() }
  }, delay)
  return { loop, initial }
}

interface LoraLoops { autoLabel?: PollingLoop; preprocess?: PollingLoop; training?: PollingLoop; labelDiscovery?: PollingLoop; preprocessDiscovery?: PollingLoop; initial?: Promise<void> }
const pollLoops = new WeakMap<object, LoraLoops>()
function loopsFor(owner: object): LoraLoops {
  let loops = pollLoops.get(owner)
  if (!loops) { loops = {}; pollLoops.set(owner, loops) }
  return loops
}

export const useLoraTrainingStore = defineStore('loraTraining', {
  state: () => ({
    // Dataset
    samples: [] as api.DatasetSample[],
    datasetPath: '',
    scanning: false,
    scanError: '',

    // Auto-label
    autoLabelStarting: false,
    autoLabelRunning: false,
    autoLabelTaskId: '',
    autoLabelCurrent: 0,
    autoLabelTotal: 0,
    autoLabelProgressMsg: '',
    autoLabelLastSample: null as api.DatasetSample | null,
    autoLabelError: '',
    autoLabelReconnecting: false,
    autoLabelInterrupted: false,
    autoLabelSubmissionUnknown: false,
    discoveredAutoLabel: null as api.LatestAutoLabelStatus | null,

    // Preprocess
    preprocessStarting: false,
    preprocessRunning: false,
    preprocessTaskId: '',
    preprocessCurrent: 0,
    preprocessTotal: 0,
    preprocessNumTensors: null as number | null,
    preprocessOutputDir: '',
    preprocessTaskOutputDir: '',
    preprocessError: '',
    preprocessReconnecting: false,
    preprocessInterrupted: false,
    preprocessSubmissionUnknown: false,
    discoveredPreprocess: null as api.LatestPreprocessStatus | null,

    // Training
    trainingStarting: false,
    training: null as api.TrainingStatus | null,
    trainingError: '',
    trainingReconnecting: false,
    trainingInterrupted: false,
    trainingSubmissionUnknown: false,
    trainingTensorDir: '',
    discoveredTraining: null as api.TrainingStatus | null,

    // Export
    exporting: false,
    exportError: '',
    lastExportedPath: '',
    _backgroundGeneration: 0,
    _backgroundWatching: false,
    _datasetGeneration: 0,
  }),
  getters: {
    currentRecoveryContext(state): LoraRecoveryContext {
      return { datasetPath: state.datasetPath, autoLabelTaskId: state.autoLabelTaskId, preprocessTaskId: state.preprocessTaskId,
        preprocessOutputDir: state.preprocessTaskOutputDir, trainingTensorDir: state.trainingTensorDir }
    },
    nativeBusy(state): boolean {
      return state.autoLabelStarting || state.autoLabelRunning || state.autoLabelReconnecting || state.autoLabelSubmissionUnknown
        || state.preprocessStarting || state.preprocessRunning || state.preprocessReconnecting || state.preprocessSubmissionUnknown
        || state.trainingStarting || state.trainingReconnecting || state.trainingSubmissionUnknown || !!state.training?.is_training
        || state.discoveredAutoLabel?.status === 'running' || state.discoveredPreprocess?.status === 'running' || !!state.discoveredTraining?.is_training
    },
    isTraining(state): boolean {
      return state.training?.is_training ?? false
    },
    totalEpochs(state): number {
      const epochs = state.training?.config.epochs ?? state.training?.config.train_epochs
      return typeof epochs === 'number' && Number.isFinite(epochs) ? epochs : 0
    },
  },
  actions: {
    restoreRecoveryContext(context: LoraRecoveryContext) {
      const watching = this._backgroundWatching
      this.stopBackgroundTasks()
      this._datasetGeneration++
      this.samples = []
      this.datasetPath = context.datasetPath
      this.autoLabelStarting = false
      this.autoLabelTaskId = context.autoLabelTaskId || ''
      this.autoLabelRunning = !!this.autoLabelTaskId
      this.autoLabelReconnecting = false
      this.autoLabelInterrupted = false
      this.autoLabelSubmissionUnknown = false
      this.autoLabelCurrent = 0
      this.autoLabelTotal = 0
      this.autoLabelLastSample = null
      this.autoLabelError = ''
      this.discoveredAutoLabel = null
      this.preprocessTaskId = context.preprocessTaskId || ''
      this.preprocessStarting = false
      this.preprocessRunning = !!this.preprocessTaskId
      this.preprocessTaskOutputDir = context.preprocessOutputDir || ''
      this.preprocessOutputDir = ''
      this.preprocessReconnecting = false
      this.preprocessInterrupted = false
      this.preprocessSubmissionUnknown = false
      this.preprocessCurrent = 0
      this.preprocessTotal = 0
      this.preprocessNumTensors = null
      this.preprocessError = ''
      this.discoveredPreprocess = null
      this.trainingTensorDir = context.trainingTensorDir || ''
      this.trainingStarting = false
      this.training = null
      this.trainingError = ''
      this.trainingInterrupted = false
      this.trainingReconnecting = false
      this.trainingSubmissionUnknown = false
      this.discoveredTraining = null
      if (watching) this.startBackgroundTasks()
    },
    async scanDataset(req: api.ScanDatasetRequest) {
      this.scanning = true
      this.scanError = ''
      try {
        const res = await api.scanDataset(req)
        this.samples = res.samples
        return res
      } catch (err) {
        this.scanError = errorText(err)
        throw err
      } finally {
        this.scanning = false
      }
    },
    async loadDataset(path: string) {
      this.scanning = true
      this.scanError = ''
      try {
        const res = await api.loadDataset(path)
        this.samples = res.samples
        this.datasetPath = path
        return res
      } catch (err) {
        this.scanError = errorText(err)
        throw err
      } finally {
        this.scanning = false
      }
    },
    async saveDataset(req: api.SaveDatasetRequest) {
      await api.saveDataset(req)
      this.datasetPath = req.save_path
    },
    async updateSample(idx: number, req: api.UpdateSampleRequest) {
      const updated = await api.updateSample(idx, req)
      const i = this.samples.findIndex((s) => s.index === idx)
      if (i !== -1) this.samples[i] = updated
    },

    async startAutoLabel(req: api.AutoLabelRequest, submit: (req: api.AutoLabelRequest) => Promise<api.AsyncTaskStarted> = api.startAutoLabel) {
      if (this.autoLabelStarting || this.autoLabelRunning || this.autoLabelSubmissionUnknown) return
      const datasetGeneration = this._datasetGeneration
      const datasetPath = this.datasetPath
      this.autoLabelError = ''
      this.autoLabelLastSample = null
      this.autoLabelInterrupted = false
      this.autoLabelReconnecting = false
      this.autoLabelStarting = true
      loopsFor(this).labelDiscovery?.stop()
      try {
        const res = await submit(req)
        if (datasetGeneration !== this._datasetGeneration || datasetPath !== this.datasetPath) return
        if (!res.task_id || res.total === 0) {
          this.autoLabelError = res.message || t('storeErrors.noSamplesToLabel')
          return
        }
        this.autoLabelTaskId = res.task_id
        this.autoLabelRunning = true
        this.autoLabelCurrent = 0
        this.autoLabelTotal = res.total
        if (this._backgroundWatching) this._pollAutoLabel()
      } catch (err) {
        if (datasetGeneration !== this._datasetGeneration || datasetPath !== this.datasetPath) return
        if (submissionRejected(err)) this.autoLabelError = errorText(err)
        else {
          this.autoLabelTaskId = ''
          this.autoLabelSubmissionUnknown = true
          this.autoLabelReconnecting = true
          if (this._backgroundWatching) this._discoverAutoLabel()
        }
      } finally {
        if (datasetGeneration === this._datasetGeneration) this.autoLabelStarting = false
      }
    },
    _pollAutoLabel() {
      const loops = loopsFor(this)
      loops.autoLabel?.stop()
      const taskId = this.autoLabelTaskId
      const datasetPath = this.datasetPath
      let restoreSamples = this.samples.length === 0 && !!datasetPath
      const { loop, initial } = observedLoop(async (context) => {
        try {
          const st = await api.autoLabelStatus(taskId, context.signal)
          if (!context.isCurrent() || this.autoLabelTaskId !== taskId || datasetPath !== this.datasetPath) return false
          this.autoLabelReconnecting = false
          if (st.task_id !== taskId || (st.save_path && datasetPath && !samePath(st.save_path, datasetPath))) {
            this.discoveredAutoLabel = st
            this.autoLabelRunning = false
            this.autoLabelInterrupted = true
            return false
          }
          this._applyAutoLabelStatus(st)
          if (restoreSamples && st.status === 'running') {
            const res = await api.getSamples(context.signal)
            if (!context.isCurrent() || this.autoLabelTaskId !== taskId || datasetPath !== this.datasetPath) return false
            this.samples = res.samples
            restoreSamples = false
          }
          if (st.status !== 'running') return false
        } catch (err) {
          if (!context.isCurrent()) return false
          if (err instanceof ApiError && err.status === 404) {
            this.autoLabelRunning = false
            this.autoLabelReconnecting = false
            this.autoLabelInterrupted = true
            if (this._backgroundWatching) await this._discoverAutoLabel()
            return false
          }
          this.autoLabelReconnecting = true
        }
        return true
      }, AUTOLABEL_POLL_MS)
      loops.autoLabel = loop
      loop.start()
      return initial
    },
    _applyAutoLabelStatus(st: api.AutoLabelStatus) {
      this.discoveredAutoLabel = st
      this.autoLabelCurrent = st.current
      this.autoLabelTotal = st.total
      this.autoLabelProgressMsg = st.progress
      this.autoLabelRunning = st.status === 'running'
      this.autoLabelSubmissionUnknown = false
      this.autoLabelInterrupted = false
      if (st.last_updated_sample) this.autoLabelLastSample = st.last_updated_sample
      if (st.status === 'completed' && st.result) this.samples = st.result.samples
      if (st.status === 'failed') this.autoLabelError = st.error || t('storeErrors.labelingFailed')
    },
    _discoverAutoLabel() {
      const loops = loopsFor(this)
      loops.labelDiscovery?.stop()
      this.autoLabelReconnecting = true
      const datasetPath = this.datasetPath
      const { loop, initial } = observedLoop(async context => {
        try {
          const st = await api.latestAutoLabelStatus(context.signal)
          if (!context.isCurrent() || datasetPath !== this.datasetPath) return false
          this.discoveredAutoLabel = st
          this.autoLabelReconnecting = false
          if (st.status !== 'idle' && ((st.task_id === this.autoLabelTaskId && !this.autoLabelInterrupted) || (!this.autoLabelTaskId && st.status === 'running' && st.save_path && samePath(st.save_path, datasetPath)))) {
            if (st.save_path && datasetPath && !samePath(st.save_path, datasetPath)) return st.status === 'running'
            this.autoLabelTaskId = st.task_id
            this._applyAutoLabelStatus(st)
            if (st.status === 'running') await this._pollAutoLabel()
            return false
          }
          if (this.autoLabelSubmissionUnknown && st.status !== 'running') {
            this.autoLabelSubmissionUnknown = false
            this.autoLabelInterrupted = true
          }
          return st.status === 'running'
        } catch {
          if (!context.isCurrent()) return false
          this.autoLabelReconnecting = true
          return true
        }
      }, AUTOLABEL_POLL_MS)
      loops.labelDiscovery = loop
      loop.start()
      return initial
    },

    async startPreprocess(req: api.PreprocessRequest, submit: (req: api.PreprocessRequest) => Promise<api.AsyncTaskStarted> = api.startPreprocess) {
      if (this.preprocessStarting || this.preprocessRunning || this.preprocessSubmissionUnknown) return
      const datasetGeneration = this._datasetGeneration
      const datasetPath = this.datasetPath
      this.preprocessError = ''
      this.preprocessInterrupted = false
      this.preprocessReconnecting = false
      this.preprocessStarting = true
      loopsFor(this).preprocessDiscovery?.stop()
      try {
        const res = await submit(req)
        if (datasetGeneration !== this._datasetGeneration || datasetPath !== this.datasetPath) return
        if (!res.task_id || res.total === 0) {
          this.preprocessError = res.message || t('storeErrors.noLabeledSamples')
          return
        }
        this.preprocessTaskId = res.task_id
        this.preprocessTaskOutputDir = req.output_dir
        this.preprocessRunning = true
        this.preprocessCurrent = 0
        this.preprocessTotal = res.total
        this.preprocessNumTensors = null
        this.preprocessOutputDir = ''
        if (this._backgroundWatching) this._pollPreprocess(req.output_dir)
      } catch (err) {
        if (datasetGeneration !== this._datasetGeneration || datasetPath !== this.datasetPath) return
        if (submissionRejected(err)) this.preprocessError = errorText(err)
        else {
          this.preprocessTaskId = ''
          this.preprocessTaskOutputDir = req.output_dir
          this.preprocessSubmissionUnknown = true
          this.preprocessReconnecting = true
          if (this._backgroundWatching) this._discoverPreprocess()
        }
      } finally {
        if (datasetGeneration === this._datasetGeneration) this.preprocessStarting = false
      }
    },
    _pollPreprocess(outputDir: string) {
      const loops = loopsFor(this)
      loops.preprocess?.stop()
      const taskId = this.preprocessTaskId
      const datasetPath = this.datasetPath
      let restoreSamples = this.samples.length === 0 && !!datasetPath
      const { loop, initial } = observedLoop(async (context) => {
        try {
          const st = await api.preprocessStatus(taskId, context.signal)
          if (!context.isCurrent() || this.preprocessTaskId !== taskId || datasetPath !== this.datasetPath) return false
          this.preprocessReconnecting = false
          if (st.task_id !== taskId || (st.result?.output_dir && outputDir && !samePath(st.result.output_dir, outputDir))) {
            this.discoveredPreprocess = st
            this.preprocessRunning = false
            this.preprocessInterrupted = true
            return false
          }
          this.discoveredPreprocess = st
          this.preprocessCurrent = st.current
          this.preprocessTotal = st.total
          this.preprocessRunning = st.status === 'running'
          if (restoreSamples && st.status === 'running') {
            const res = await api.getSamples(context.signal)
            if (!context.isCurrent() || this.preprocessTaskId !== taskId || datasetPath !== this.datasetPath) return false
            this.samples = res.samples
            restoreSamples = false
          }
          if (st.status === 'completed') {
            this.preprocessRunning = false
            const produced = st.result?.num_tensors
            this.preprocessNumTensors = produced ?? null
            if (produced == null || produced <= 0) {
              this.preprocessOutputDir = ''
              this.preprocessError = produced === 0 ? t('lora.preprocessNoTensors') : t('storeErrors.preprocessFailed')
              return false
            }
            this.preprocessOutputDir = st.result?.output_dir || outputDir
            this.preprocessError = produced < st.total ? t('lora.preprocessPartial') : ''
            return false
          }
          if (st.status === 'failed') {
            this.preprocessRunning = false
            this.preprocessError = st.error || t('storeErrors.preprocessFailed')
            return false
          }
        } catch (err) {
          if (!context.isCurrent()) return false
          if (err instanceof ApiError && err.status === 404) {
            this.preprocessRunning = false
            this.preprocessReconnecting = false
            this.preprocessInterrupted = true
            if (this._backgroundWatching) await this._discoverPreprocess()
            return false
          }
          this.preprocessReconnecting = true
        }
        return true
      }, PREPROCESS_POLL_MS)
      loops.preprocess = loop
      loop.start()
      return initial
    },
    _discoverPreprocess() {
      const loops = loopsFor(this)
      loops.preprocessDiscovery?.stop()
      this.preprocessReconnecting = true
      const datasetPath = this.datasetPath
      const { loop, initial } = observedLoop(async context => {
        try {
          const st = await api.latestPreprocessStatus(context.signal)
          if (!context.isCurrent() || datasetPath !== this.datasetPath) return false
          this.discoveredPreprocess = st
          this.preprocessReconnecting = false
          if (st.status !== 'idle' && st.task_id === this.preprocessTaskId && !this.preprocessInterrupted) {
            await this._pollPreprocess(this.preprocessTaskOutputDir)
            return false
          }
          if (this.preprocessSubmissionUnknown && st.status !== 'running') {
            this.preprocessSubmissionUnknown = false
            this.preprocessInterrupted = true
          }
          return st.status === 'running'
        } catch {
          if (!context.isCurrent()) return false
          this.preprocessReconnecting = true
          return true
        }
      }, PREPROCESS_POLL_MS)
      loops.preprocessDiscovery = loop
      loop.start()
      return initial
    },

    async startTraining(req: api.StartLoraTrainingRequest) {
      if (this.trainingStarting || this.isTraining || this.trainingSubmissionUnknown) return
      const datasetGeneration = this._datasetGeneration
      this.trainingError = ''
      this.trainingInterrupted = false
      this.trainingReconnecting = false
      this.trainingTensorDir = req.tensor_dir
      this.trainingStarting = true
      try {
        await api.startLoraTraining(req)
        if (datasetGeneration !== this._datasetGeneration) return
        if (this._backgroundWatching) this._ensureTrainingPoll()
      } catch (err) {
        if (datasetGeneration !== this._datasetGeneration) return
        if (submissionRejected(err)) this.trainingError = errorText(err)
        else {
          this.trainingSubmissionUnknown = true
          this.trainingReconnecting = true
          if (this._backgroundWatching) this._ensureTrainingPoll()
        }
      } finally {
        if (datasetGeneration === this._datasetGeneration) this.trainingStarting = false
      }
    },
    async stopTraining() {
      try {
        await api.stopTraining()
      } catch (err) {
        this.trainingError = errorText(err)
      }
    },
    async refreshTrainingStatus(context?: PollContext) {
      const generation = this._backgroundGeneration
      try {
        const training = await api.trainingStatus(context?.signal)
        if (generation !== this._backgroundGeneration || (context && !context.isCurrent())) return
        this.trainingReconnecting = false
        this.discoveredTraining = training
        if (this.training && (this.training.is_training || this.trainingInterrupted) && !training.is_training && !training.tensor_dir && training.start_time === null) {
          this.training = { ...this.training, is_training: false, should_stop: false }
          this.trainingInterrupted = true
          this.trainingSubmissionUnknown = false
          return
        }
        if (this.trainingTensorDir && training.tensor_dir && !samePath(this.trainingTensorDir, training.tensor_dir)) {
          if (this.training) this.training = { ...this.training, is_training: false, should_stop: false }
          this.trainingInterrupted = true
          this.trainingSubmissionUnknown = false
          return
        }
        this.training = training
        if (training.is_training) {
          this.trainingTensorDir = training.tensor_dir
          this.trainingInterrupted = false
          this.trainingSubmissionUnknown = false
        } else if (this.trainingSubmissionUnknown) {
          this.trainingSubmissionUnknown = false
          this.trainingInterrupted = true
        }
        if (this.training.error) this.trainingError = this.training.error
        else if (typeof this.training.status === 'string' && this.training.status.startsWith('❌')) {
          this.trainingError = this.training.status
        }
      } catch {
        if (generation !== this._backgroundGeneration || (context && !context.isCurrent())) return
        this.trainingReconnecting = true
      }
    },
    _ensureTrainingPoll() {
      const loops = loopsFor(this)
      loops.training?.stop()
      this.trainingReconnecting = true
      const { loop, initial } = observedLoop(async (context) => {
        await this.refreshTrainingStatus(context)
        return this.trainingReconnecting || this.trainingSubmissionUnknown || !!this.discoveredTraining?.is_training
      }, TRAINING_POLL_MS)
      // A status sent before an accepted submission cannot stop observation
      // of the new job when its stale idle response finally arrives.
      loops.training = loop
      loop.start()
      return initial
    },

    async exportAndRegister(exportPath: string, loraOutputDir: string, registryName: string, addToRegistry: (name: string, path: string) => void) {
      this.exporting = true
      this.exportError = ''
      try {
        const res = await api.exportLora({ export_path: exportPath, lora_output_dir: loraOutputDir })
        const adapterPath = res.adapter_path
        addToRegistry(registryName, adapterPath)
        this.lastExportedPath = adapterPath
      } catch (err) {
        this.exportError = loraExportErrorText(err)
        throw err
      } finally {
        this.exporting = false
      }
    },

    startBackgroundTasks(): Promise<void> {
      const loops = loopsFor(this)
      if (this._backgroundWatching) return loops.initial ?? Promise.resolve()
      this._backgroundWatching = true
      loops.initial = Promise.all([
        this.autoLabelRunning && this.autoLabelTaskId ? this._pollAutoLabel() : this._discoverAutoLabel(),
        this.preprocessRunning && this.preprocessTaskId ? this._pollPreprocess(this.preprocessTaskOutputDir) : this._discoverPreprocess(),
        this._ensureTrainingPoll(),
      ]).then(() => {})
      return loops.initial
    },
    stopBackgroundTasks() {
      this._backgroundGeneration++
      this._backgroundWatching = false
      const loops = loopsFor(this)
      loops.autoLabel?.stop()
      loops.preprocess?.stop()
      loops.training?.stop()
      loops.labelDiscovery?.stop()
      loops.preprocessDiscovery?.stop()
    },
  },
})

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useLoraTrainingStore, import.meta.hot))
}
