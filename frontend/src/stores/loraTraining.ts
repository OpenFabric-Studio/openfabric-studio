import { acceptHMRUpdate, defineStore } from 'pinia'
import * as api from '../api/aceStepTraining'
import { i18n } from '../i18n'
import { createPollingLoop, type PollContext, type PollingLoop } from '../composables/polling'

const t = i18n.global.t

const AUTOLABEL_POLL_MS = 2000
const PREPROCESS_POLL_MS = 2000
const TRAINING_POLL_MS = 3000
interface LoraLoops { autoLabel?: PollingLoop; preprocess?: PollingLoop; training?: PollingLoop }
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

    // Preprocess
    preprocessStarting: false,
    preprocessRunning: false,
    preprocessTaskId: '',
    preprocessCurrent: 0,
    preprocessTotal: 0,
    preprocessOutputDir: '',
    preprocessError: '',

    // Training
    trainingStarting: false,
    training: null as api.TrainingStatus | null,
    trainingError: '',

    // Export
    exporting: false,
    exportError: '',
    lastExportedPath: '',
    _backgroundGeneration: 0,
  }),
  getters: {
    isTraining(state): boolean {
      return state.training?.is_training ?? false
    },
    totalEpochs(state): number {
      const epochs = state.training?.config.epochs ?? state.training?.config.train_epochs
      return typeof epochs === 'number' && Number.isFinite(epochs) ? epochs : 0
    },
  },
  actions: {
    async scanDataset(req: api.ScanDatasetRequest) {
      this.scanning = true
      this.scanError = ''
      try {
        const res = await api.scanDataset(req)
        this.samples = res.samples
        return res
      } catch (err) {
        this.scanError = err instanceof Error ? err.message : String(err)
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
        this.scanError = err instanceof Error ? err.message : String(err)
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

    async startAutoLabel(req: api.AutoLabelRequest) {
      const generation = this._backgroundGeneration
      this.autoLabelError = ''
      this.autoLabelLastSample = null
      this.autoLabelStarting = true
      try {
        const res = await api.startAutoLabel(req)
        if (!res.task_id || res.total === 0) {
          this.autoLabelError = res.message || t('storeErrors.noSamplesToLabel')
          return
        }
        this.autoLabelTaskId = res.task_id
        this.autoLabelRunning = true
        this.autoLabelCurrent = 0
        this.autoLabelTotal = res.total
        if (generation === this._backgroundGeneration) this._pollAutoLabel()
      } catch (err) {
        this.autoLabelError = err instanceof Error ? err.message : String(err)
      } finally {
        this.autoLabelStarting = false
      }
    },
    _pollAutoLabel() {
      const loops = loopsFor(this)
      loops.autoLabel?.stop()
      const taskId = this.autoLabelTaskId
      const loop = createPollingLoop(async (context) => {
        try {
          const st = await api.autoLabelStatus(taskId, context.signal)
          if (!context.isCurrent() || this.autoLabelTaskId !== taskId) return false
          this.autoLabelCurrent = st.current
          this.autoLabelTotal = st.total
          this.autoLabelProgressMsg = st.progress
          if (st.last_updated_sample) this.autoLabelLastSample = st.last_updated_sample
          if (st.status === 'completed') {
            this.autoLabelRunning = false
            if (st.result) this.samples = st.result.samples
            return false
          }
          if (st.status === 'failed') {
            this.autoLabelRunning = false
            this.autoLabelError = st.error || t('storeErrors.labelingFailed')
            return false
          }
        } catch (err) {
          if (!context.isCurrent()) return false
          this.autoLabelRunning = false
          this.autoLabelError = err instanceof Error ? err.message : String(err)
          return false
        }
        return true
      }, AUTOLABEL_POLL_MS)
      loops.autoLabel = loop
      loop.start()
    },

    async startPreprocess(req: api.PreprocessRequest) {
      const generation = this._backgroundGeneration
      this.preprocessError = ''
      this.preprocessStarting = true
      try {
        const res = await api.startPreprocess(req)
        if (!res.task_id || res.total === 0) {
          this.preprocessError = res.message || t('storeErrors.noLabeledSamples')
          return
        }
        this.preprocessTaskId = res.task_id
        this.preprocessRunning = true
        this.preprocessCurrent = 0
        this.preprocessTotal = res.total
        if (generation === this._backgroundGeneration) this._pollPreprocess(req.output_dir)
      } catch (err) {
        this.preprocessError = err instanceof Error ? err.message : String(err)
      } finally {
        this.preprocessStarting = false
      }
    },
    _pollPreprocess(outputDir: string) {
      const loops = loopsFor(this)
      loops.preprocess?.stop()
      const taskId = this.preprocessTaskId
      const loop = createPollingLoop(async (context) => {
        try {
          const st = await api.preprocessStatus(taskId, context.signal)
          if (!context.isCurrent() || this.preprocessTaskId !== taskId) return false
          this.preprocessCurrent = st.current
          this.preprocessTotal = st.total
          if (st.status === 'completed') {
            this.preprocessRunning = false
            const produced = st.result?.num_tensors
            const message = st.result?.message || st.progress
            if (produced === 0) {
              this.preprocessError = message || t('storeErrors.preprocessFailed')
              return false
            }
            this.preprocessOutputDir = st.result?.output_dir || outputDir
            if (message && /failed/i.test(message)) this.preprocessError = message
            return false
          }
          if (st.status === 'failed') {
            this.preprocessRunning = false
            this.preprocessError = st.error || t('storeErrors.preprocessFailed')
            return false
          }
        } catch (err) {
          if (!context.isCurrent()) return false
          this.preprocessRunning = false
          this.preprocessError = err instanceof Error ? err.message : String(err)
          return false
        }
        return true
      }, PREPROCESS_POLL_MS)
      loops.preprocess = loop
      loop.start()
    },

    async startTraining(req: api.StartLoraTrainingRequest) {
      const generation = this._backgroundGeneration
      this.trainingError = ''
      this.trainingStarting = true
      try {
        await api.startLoraTraining(req)
        if (generation === this._backgroundGeneration) this._ensureTrainingPoll()
        await this.refreshTrainingStatus()
      } catch (err) {
        this.trainingError = err instanceof Error ? err.message : String(err)
      } finally {
        this.trainingStarting = false
      }
    },
    async stopTraining() {
      try {
        await api.stopTraining()
      } catch (err) {
        this.trainingError = err instanceof Error ? err.message : String(err)
      }
    },
    async refreshTrainingStatus(context?: PollContext) {
      try {
        const training = await api.trainingStatus(context?.signal)
        if (context && !context.isCurrent()) return
        this.training = training
        if (this.training.error) this.trainingError = this.training.error
        else if (typeof this.training.status === 'string' && this.training.status.startsWith('❌')) {
          this.trainingError = this.training.status
        }
      } catch {
        // Model may be offline - leave last known state as-is.
      }
    },
    _ensureTrainingPoll() {
      const loops = loopsFor(this)
      if (!loops.training) loops.training = createPollingLoop(async (context) => {
        await this.refreshTrainingStatus(context)
        return this.training?.is_training ?? false
      }, TRAINING_POLL_MS)
      loops.training.start()
    },

    async exportAndRegister(exportPath: string, loraOutputDir: string, registryName: string, addToRegistry: (name: string, path: string) => void) {
      this.exporting = true
      this.exportError = ''
      try {
        const res = await api.exportLora({ export_path: exportPath, lora_output_dir: loraOutputDir })
        const adapterPath = res.export_path.replace(/[\\/]+$/, '') + '/adapter'
        addToRegistry(registryName, adapterPath)
        this.lastExportedPath = adapterPath
      } catch (err) {
        this.exportError = err instanceof Error ? err.message : String(err)
        throw err
      } finally {
        this.exporting = false
      }
    },

    stopBackgroundTasks() {
      this._backgroundGeneration++
      const loops = loopsFor(this)
      loops.autoLabel?.stop()
      loops.preprocess?.stop()
      loops.training?.stop()
    },
  },
})

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useLoraTrainingStore, import.meta.hot))
}
