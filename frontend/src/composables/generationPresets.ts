import { isRecord } from '../api/nativeValidation'
import type { CotMode } from '../api/yue2'

export const SAMPLING_KEYS = ['temperature', 'top_p', 'top_k', 'repetition_penalty', 'penalty_window', 'min_tokens', 'max_tokens'] as const
export type SamplingKey = typeof SAMPLING_KEYS[number]
export type SamplingSettings = Record<SamplingKey, number | null>

export interface AcePreset {
  name: string
  mode: 'simple' | 'custom'
  simpleQuery: string
  customPrompt: string
  instrumental: boolean
  customLyrics: string
  duration: number
  durationAuto: boolean
  audioFormat: 'mp3' | 'wav' | 'flac'
  bpm: number | null
  keyScale: string
  timeSignature: string
  vocalLanguage: string
  inferenceSteps: number | null
  guidanceScale: number | null
  selectedModel: string
}

export interface Yue2Preset {
  name: string
  lyrics: string
  style: string
  cot: CotMode
  precision: 'q8_0' | 'q4_0'
  abc: string
  cfgScale: number | null
  numInferenceSteps: number | null
  semantic: SamplingSettings
  abcSampling: SamplingSettings
}

const text = (value: unknown) => typeof value === 'string' ? value : ''
export const finiteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const numeric = (value: unknown) => finiteNumber(value) ? value : null

export function samplingSettings(value: unknown): SamplingSettings {
  const row = isRecord(value) ? value : {}
  return {
    temperature: numeric(row.temperature), top_p: numeric(row.top_p), top_k: numeric(row.top_k),
    repetition_penalty: numeric(row.repetition_penalty), penalty_window: numeric(row.penalty_window),
    min_tokens: numeric(row.min_tokens), max_tokens: numeric(row.max_tokens),
  }
}

export function parseAcePresets(value: unknown): AcePreset[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((row: unknown) => {
    if (!isRecord(row) || typeof row.name !== 'string' || !row.name.trim()) return []
    if (row.mode !== 'simple' && row.mode !== 'custom') return []
    if (row.audioFormat !== 'mp3' && row.audioFormat !== 'wav' && row.audioFormat !== 'flac') return []
    return [{
      name: row.name, mode: row.mode, audioFormat: row.audioFormat,
      simpleQuery: text(row.simpleQuery), customPrompt: text(row.customPrompt), customLyrics: text(row.customLyrics),
      instrumental: row.instrumental === true, duration: finiteNumber(row.duration) ? Math.min(300, Math.max(10, row.duration)) : 120,
      durationAuto: row.durationAuto === true, bpm: numeric(row.bpm), keyScale: text(row.keyScale),
      timeSignature: text(row.timeSignature), vocalLanguage: text(row.vocalLanguage), inferenceSteps: numeric(row.inferenceSteps),
      guidanceScale: numeric(row.guidanceScale), selectedModel: text(row.selectedModel),
    }]
  })
}

export function parseYue2Presets(value: unknown): Yue2Preset[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((row: unknown) => {
    if (!isRecord(row) || typeof row.name !== 'string' || !row.name.trim()) return []
    if (row.cot !== 'off' && row.cot !== 'melody' && row.cot !== 'full') return []
    if (row.precision !== 'q8_0' && row.precision !== 'q4_0') return []
    return [{
      name: row.name, lyrics: text(row.lyrics), style: text(row.style), cot: row.cot, precision: row.precision,
      abc: text(row.abc), cfgScale: numeric(row.cfgScale), numInferenceSteps: numeric(row.numInferenceSteps),
      semantic: samplingSettings(row.semantic), abcSampling: samplingSettings(row.abcSampling),
    }]
  })
}
