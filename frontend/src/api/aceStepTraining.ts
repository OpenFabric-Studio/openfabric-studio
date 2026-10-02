import { apiFetch, apiJson } from './http'
import * as v from './nativeValidation'

const BASE = '/api/ace'

export interface DatasetSample {
  index: number
  filename: string
  audio_path: string
  duration: number
  caption: string
  genre: string
  prompt_override: 'caption' | 'genre' | null
  lyrics: string
  bpm: number | null
  keyscale: string
  timesignature: string
  language: string
  is_instrumental: boolean
  labeled: boolean
}

export interface ScanDatasetRequest {
  audio_dir: string
  dataset_name?: string
  custom_tag?: string
  tag_position?: 'prepend' | 'append' | 'replace'
  all_instrumental?: boolean
}

export interface ScanDatasetResponse {
  message: string
  num_samples: number
  samples: DatasetSample[]
}

export interface SaveDatasetRequest {
  save_path: string
  dataset_name?: string
  custom_tag?: string
  tag_position?: 'prepend' | 'append' | 'replace'
  all_instrumental?: boolean
  genre_ratio?: number
}

export interface UpdateSampleRequest {
  caption?: string
  genre?: string
  prompt_override?: 'caption' | 'genre' | null
  lyrics?: string
  bpm?: number
  keyscale?: string
  timesignature?: string
  language?: string
  is_instrumental?: boolean
}

export interface AutoLabelRequest {
  skip_metas?: boolean
  format_lyrics?: boolean
  transcribe_lyrics?: boolean
  only_unlabeled?: boolean
  lm_model_path?: string
  save_path?: string
  chunk_size?: number
  batch_size?: number
}

export interface AsyncTaskStarted {
  task_id: string
  message: string
  total: number
}

export interface AutoLabelStatus {
  task_id: string
  status: 'running' | 'completed' | 'failed'
  progress: string
  current: number
  total: number
  save_path?: string
  last_updated_index?: number
  last_updated_sample?: DatasetSample
  result?: { message: string; labeled_count: number; samples: DatasetSample[] }
  error?: string
}

export interface PreprocessRequest {
  output_dir: string
  skip_existing?: boolean
}

export interface PreprocessStatus {
  task_id: string
  status: 'running' | 'completed' | 'failed'
  progress: string
  current: number
  total: number
  result?: { message: string; output_dir: string; num_tensors: number }
  error?: string
}

export interface StartLoraTrainingRequest {
  tensor_dir: string
  lora_rank?: number
  lora_alpha?: number
  lora_dropout?: number
  learning_rate?: number
  train_epochs?: number
  train_batch_size?: number
  gradient_accumulation?: number
  save_every_n_epochs?: number
  training_seed?: number
  lora_output_dir?: string
  use_fp8?: boolean
  gradient_checkpointing?: boolean
}

export interface StartTrainingResponse {
  message: string
  tensor_dir: string
  output_dir: string
  config: TrainingConfig
}

export interface TrainingConfig {
  lora_rank?: number
  lora_alpha?: number
  learning_rate?: number
  epochs?: number
  train_epochs?: number
}

export interface TrainingStatus {
  is_training: boolean
  should_stop: boolean
  current_step: number
  current_loss: number | null
  status: string
  config: TrainingConfig
  tensor_dir: string
  loss_history: { step: number; loss: number }[]
  tensorboard_url: string | null
  tensorboard_logdir: string | null
  training_log: string
  start_time: number | null
  current_epoch: number
  steps_per_second: number
  estimated_time_remaining: number
  error: string | null
}

export interface ExportLoraRequest {
  export_path: string
  lora_output_dir: string
}

export interface ExportLoraResponse {
  message: string
  export_path: string
  source: string
}

export function parseDatasetSample(value: unknown): DatasetSample {
  const row = v.record(value)
  return {
    index: v.number(row.index), filename: v.string(row.filename), audio_path: v.string(row.audio_path),
    duration: v.number(row.duration), caption: v.string(row.caption), genre: v.string(row.genre),
    prompt_override: row.prompt_override == null ? null : v.literal(row.prompt_override, ['caption', 'genre']),
    lyrics: v.string(row.lyrics), bpm: v.nullableNumber(row.bpm), keyscale: v.string(row.keyscale),
    timesignature: v.string(row.timesignature), language: v.string(row.language),
    is_instrumental: v.boolean(row.is_instrumental), labeled: v.boolean(row.labeled),
  }
}

function parseScan(value: unknown): ScanDatasetResponse {
  const row = v.record(value)
  return { message: v.string(row.message), num_samples: v.number(row.num_samples), samples: v.array(row.samples, parseDatasetSample) }
}

function parseMessage(value: unknown): { message: string } {
  return { message: v.string(v.record(value).message) }
}

function parseStarted(value: unknown): AsyncTaskStarted {
  const row = v.record(value)
  return { task_id: v.string(row.task_id), message: v.string(row.message), total: v.number(row.total) }
}

function optional<T>(value: unknown, parse: (item: unknown) => T): T | undefined {
  return value == null ? undefined : parse(value)
}

function parseConfig(value: unknown): TrainingConfig {
  const row = v.record(value)
  return {
    lora_rank: optional(row.lora_rank, v.number), lora_alpha: optional(row.lora_alpha, v.number),
    learning_rate: optional(row.learning_rate, v.number), epochs: optional(row.epochs, v.number), train_epochs: optional(row.train_epochs, v.number),
  }
}

function parseAutoLabel(value: unknown): AutoLabelStatus {
  const row = v.record(value)
  return {
    task_id: v.string(row.task_id), status: v.literal(row.status, ['running', 'completed', 'failed']),
    progress: v.string(row.progress), current: v.number(row.current), total: v.number(row.total),
    save_path: optional(row.save_path, v.string), last_updated_index: optional(row.last_updated_index, v.number),
    last_updated_sample: optional(row.last_updated_sample, (value) => {
      const sample = v.record(value)
      return parseDatasetSample({ ...sample, index: sample.index ?? row.last_updated_index })
    }), error: optional(row.error, v.string),
    result: optional(row.result, (value) => {
      const result = v.record(value)
      return { message: v.string(result.message), labeled_count: v.number(result.labeled_count), samples: v.array(result.samples, parseDatasetSample) }
    }),
  }
}

function parsePreprocess(value: unknown): PreprocessStatus {
  const row = v.record(value)
  return {
    task_id: v.string(row.task_id), status: v.literal(row.status, ['running', 'completed', 'failed']),
    progress: v.string(row.progress), current: v.number(row.current), total: v.number(row.total),
    error: optional(row.error, v.string), result: optional(row.result, (value) => {
      const result = v.record(value)
      return { message: v.string(result.message), output_dir: v.string(result.output_dir), num_tensors: v.number(result.num_tensors) }
    }),
  }
}

export function parseTrainingStatus(value: unknown): TrainingStatus {
  const row = v.record(value)
  return {
    is_training: v.boolean(row.is_training), should_stop: v.boolean(row.should_stop), current_step: v.number(row.current_step),
    current_loss: v.nullableNumber(row.current_loss), status: v.string(row.status), config: parseConfig(row.config), tensor_dir: v.string(row.tensor_dir),
    loss_history: v.array(row.loss_history, (value) => { const loss = v.record(value); return { step: v.number(loss.step), loss: v.number(loss.loss) } }),
    tensorboard_url: v.nullableString(row.tensorboard_url), tensorboard_logdir: v.nullableString(row.tensorboard_logdir),
    training_log: v.string(row.training_log), start_time: v.nullableNumber(row.start_time), current_epoch: v.number(row.current_epoch),
    steps_per_second: v.number(row.steps_per_second), estimated_time_remaining: v.number(row.estimated_time_remaining), error: v.nullableString(row.error),
  }
}

async function post<T>(path: string, body: unknown, parse: (value: unknown) => T): Promise<T> {
  return parse(v.nativePayload(await apiJson(`${BASE}${path}`, body)))
}

async function get<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  return parse(v.nativePayload(await apiFetch(`${BASE}${path}`, { signal })))
}

export const scanDataset = (req: ScanDatasetRequest) => post('/v1/dataset/scan', req, parseScan)
export const loadDataset = (dataset_path: string) => post('/v1/dataset/load', { dataset_path }, parseScan)
export const saveDataset = (req: SaveDatasetRequest) => post('/v1/dataset/save', req, parseMessage)
export const getSamples = () => get('/v1/dataset/samples', (value) => ({ samples: v.array(v.record(value).samples, parseDatasetSample) }))
export const updateSample = async (idx: number, req: UpdateSampleRequest): Promise<DatasetSample> => {
  const row = v.record(v.nativePayload(await apiJson(`${BASE}/v1/dataset/sample/${idx}`, { ...req, sample_idx: idx }, 'PUT')))
  return parseDatasetSample(row.sample)
}

export const startAutoLabel = (req: AutoLabelRequest) => post('/v1/dataset/auto_label_async', req, parseStarted)
export const autoLabelStatus = (taskId: string, signal?: AbortSignal) => get(`/v1/dataset/auto_label_status/${encodeURIComponent(taskId)}`, parseAutoLabel, signal)

export const startPreprocess = (req: PreprocessRequest) => post('/v1/dataset/preprocess_async', req, parseStarted)
export const preprocessStatus = (taskId: string, signal?: AbortSignal) => get(`/v1/dataset/preprocess_status/${encodeURIComponent(taskId)}`, parsePreprocess, signal)

export const startLoraTraining = (req: StartLoraTrainingRequest) => post('/v1/training/start', req, (value): StartTrainingResponse => {
  const row = v.record(value)
  return { message: v.string(row.message), tensor_dir: v.string(row.tensor_dir), output_dir: v.string(row.output_dir), config: parseConfig(row.config) }
})
export const trainingStatus = (signal?: AbortSignal) => get('/v1/training/status', parseTrainingStatus, signal)
export const stopTraining = () => post('/v1/training/stop', {}, parseMessage)
export const exportLora = (req: ExportLoraRequest) => post('/v1/training/export', req, (value): ExportLoraResponse => {
  const row = v.record(value)
  return { message: v.string(row.message), export_path: v.string(row.export_path), source: v.string(row.source) }
})
