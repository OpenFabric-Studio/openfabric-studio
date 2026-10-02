import { apiFetch, apiJson } from './http'
import { getConfig } from './orchestrator'
import type { Yue2ModelSpecConfig } from './orchestrator'
import * as v from './nativeValidation'
import type { YueOptions } from './contracts'

const BASE = '/api/yue2'

export type Yue2ModelSpec = Yue2ModelSpecConfig

// Fallback specifications (overwritten dynamically when orchestrator config loads)
export const YUE2_MODEL: Yue2ModelSpec = {
  id: 'yue2',
  family: 'yue2',
  path: 'E:/AI/YuE2-3B/models/Yue2-3B-GGUF',
  task: 'gen',
  mode: 'offline',
}

export const SHEETSAGE_MODEL: Yue2ModelSpec = {
  id: 'sheetsage2',
  family: 'sheetsage2',
  path: 'E:/AI/YuE2-3B/models/SheetSage2-GGUF/sheetsage2-orig.gguf',
  task: 'midi',
  mode: 'offline',
}

let specsPromise: Promise<Record<string, Yue2ModelSpec>> | null = null

export async function getYue2Specs(): Promise<Record<string, Yue2ModelSpec>> {
  if (!specsPromise) {
    specsPromise = getConfig()
      .then((cfg) => {
        if (cfg?.yue2_specs) {
          if (cfg.yue2_specs.yue2) Object.assign(YUE2_MODEL, cfg.yue2_specs.yue2)
          if (cfg.yue2_specs.sheetsage2) Object.assign(SHEETSAGE_MODEL, cfg.yue2_specs.sheetsage2)
          return cfg.yue2_specs
        }
        return { yue2: YUE2_MODEL, sheetsage2: SHEETSAGE_MODEL }
      })
      .catch(() => {
        specsPromise = null
        return { yue2: YUE2_MODEL, sheetsage2: SHEETSAGE_MODEL }
      })
  }
  return specsPromise
}

export async function getYue2ModelSpec(): Promise<Yue2ModelSpec> {
  const specs = await getYue2Specs()
  return specs.yue2 || YUE2_MODEL
}

export async function getSheetSageModelSpec(): Promise<Yue2ModelSpec> {
  const specs = await getYue2Specs()
  return specs.sheetsage2 || SHEETSAGE_MODEL
}

export type CotMode = 'off' | 'melody' | 'full'

export type GenerateOptions = Pick<YueOptions, 'style' | 'cot'> & Partial<Omit<YueOptions, 'style' | 'cot'>>

export interface TaskRunResult {
  audio?: string
  text?: string
  artifacts?: Array<{ id?: string; meta?: { format?: string; extension?: string }; payload?: string }>
  timing?: { wall_ms?: number; audio_duration_ms?: number }
}

export interface HealthResponse {
  status?: string
  backend?: string
}

function modelSessionOptions(precision: 'q8_0' | 'q4_0'): Record<string, string> {
  if (precision === 'q4_0') return { 'yue2.model_gguf': 'yue2-3b-q4_0.gguf' }
  return {}
}

async function getModels(signal?: AbortSignal): Promise<Array<{ id: string; loaded: boolean }>> {
  const json = v.record(await apiFetch(`${BASE}/v1/models`, { signal }))
  return v.array(json.data, (item) => {
    const model = v.record(item)
    return { id: v.string(model.id), loaded: v.boolean(model.loaded) }
  })
}

async function loadModelSpec(spec: Yue2ModelSpec, sessionOptions?: Record<string, string>, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted()
  await apiFetch(`${BASE}/v1/models/load`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({
    id: spec.id,
    path: spec.path,
    family: spec.family,
    task: spec.task,
    mode: spec.mode,
    ...(spec.model_spec_override ? { model_spec_override: spec.model_spec_override } : {}),
    load_options: {},
    session_options: sessionOptions || {},
    }),
  })
}

export async function unloadModelId(id: string): Promise<void> {
  await apiJson(`${BASE}/v1/models/unload`, { id })
}

/**
 * Idempotent load: when sessionOptions is explicitly passed (precision
 * switch) always calls load so a resident-but-wrong-precision model gets
 * reloaded; otherwise checks the loaded list first to skip a no-op call.
 */
export async function ensureLoaded(
  specOrId?: Yue2ModelSpec | 'yue2' | 'sheetsage2' | 'muscriptor',
  sessionOptions?: Record<string, string>,
  signal?: AbortSignal,
): Promise<void> {
  signal?.throwIfAborted()
  let spec: Yue2ModelSpec
  if (!specOrId || specOrId === 'yue2') {
    spec = await getYue2ModelSpec()
  } else if (specOrId === 'sheetsage2') {
    spec = await getSheetSageModelSpec()
  } else if (typeof specOrId === 'string') {
    const specs = await getYue2Specs()
    spec = specs[specOrId] || YUE2_MODEL
  } else {
    const specs = await getYue2Specs()
    if (specs && specs[specOrId.id]) {
      spec = { ...specOrId, path: specs[specOrId.id].path }
    } else {
      spec = specOrId
    }
  }

  signal?.throwIfAborted()
  if (sessionOptions !== undefined) {
    await loadModelSpec(spec, sessionOptions, signal)
    return
  }
  const list = await getModels(signal)
  signal?.throwIfAborted()
  if (list.some((m) => m.id === spec.id && m.loaded)) return
  await loadModelSpec(spec, undefined, signal)
}

export function precisionSessionOptions(precision: 'q8_0' | 'q4_0'): Record<string, string> {
  return modelSessionOptions(precision)
}

export async function uploadFile(file: File, signal?: AbortSignal): Promise<string> {
  signal?.throwIfAborted()
  const match = /\.([A-Za-z0-9]{1,8})$/.exec(file.name)
  const filename = `upload.${(match && match[1] && match[1].toLowerCase()) || 'bin'}`
  const json = v.record(await apiFetch(`${BASE}/v1/ui/upload`, {
    method: 'POST',
    headers: {
      'Content-Type': file.type || 'application/octet-stream',
      'X-AudioCPP-Filename': filename,
    },
    body: file,
    signal,
  }))
  // The native server's JSON parser mishandles backslash escapes when a
  // Windows path it returned is echoed back in a later request body -
  // forward slashes round-trip fine, so normalize once here.
  return v.string(json.path).replace(/\\/g, '/')
}

export function parseTaskRunResult(value: unknown): TaskRunResult {
  const row = v.record(value)
  const result: TaskRunResult = {}
  if (row.audio !== undefined) result.audio = v.string(row.audio)
  if (row.text !== undefined) result.text = v.string(row.text)
  if (row.artifacts !== undefined) {
    result.artifacts = v.array(row.artifacts, (item) => {
      const artifact = v.record(item)
      const meta = artifact.meta === undefined ? undefined : v.record(artifact.meta)
      return {
        id: artifact.id === undefined ? undefined : v.string(artifact.id),
        payload: artifact.payload === undefined ? undefined : v.string(artifact.payload),
        meta: meta ? {
          format: meta.format === undefined ? undefined : v.string(meta.format),
          extension: meta.extension === undefined ? undefined : v.string(meta.extension),
        } : undefined,
      }
    })
  }
  if (row.timing !== undefined) {
    const timing = v.record(row.timing)
    result.timing = {
      wall_ms: timing.wall_ms === undefined ? undefined : v.number(timing.wall_ms),
      audio_duration_ms: timing.audio_duration_ms === undefined ? undefined : v.number(timing.audio_duration_ms),
    }
  }
  return result
}

export async function runTask(model: string, request: unknown, signal?: AbortSignal): Promise<TaskRunResult> {
  signal?.throwIfAborted()
  return parseTaskRunResult(await apiFetch(`${BASE}/v1/tasks/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, request }),
    signal,
  }))
}

export async function generateTrack(lyrics: string, seed: number, options: GenerateOptions, precision: 'q8_0' | 'q4_0', signal?: AbortSignal): Promise<TaskRunResult> {
  signal?.throwIfAborted()
  const spec = await getYue2ModelSpec()
  signal?.throwIfAborted()
  await ensureLoaded(spec, precisionSessionOptions(precision), signal)
  signal?.throwIfAborted()
  return runTask(spec.id, { lyrics, seed, options }, signal)
}

export async function extractAbcFromAudio(audioPath: string): Promise<TaskRunResult> {
  const spec = await getSheetSageModelSpec()
  await ensureLoaded(spec)
  return runTask(spec.id, { audio: audioPath, options: {} })
}

export function abcFromResult(result: TaskRunResult): string {
  if (typeof result.text === 'string' && result.text.trim()) return result.text
  for (const artifact of result.artifacts || []) {
    const format = String(artifact?.meta?.format || artifact?.meta?.extension || artifact?.id || '')
    if (/abc|score/i.test(format) && typeof artifact.payload === 'string') {
      try {
        return atob(artifact.payload)
      } catch {
        return artifact.payload
      }
    }
  }
  return ''
}

export function base64AudioBlob(data: string): Blob {
  // Slice on quartet boundaries so the temporary decoded binary string is
  // bounded. The complete encoded input, byte chunks and Blob still exist.
  const encoded = /[\t\n\f\r ]/.test(data) ? data.replace(/[\t\n\f\r ]/g, '') : data
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) throw new DOMException('Invalid base64 audio', 'InvalidCharacterError')
  const chunks: Uint8Array<ArrayBuffer>[] = []
  for (let offset = 0; offset < encoded.length; offset += 32768) {
    const binary = atob(encoded.slice(offset, offset + 32768))
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index)
    chunks.push(bytes)
  }
  return new Blob(chunks, { type: 'audio/wav' })
}

export async function health(signal?: AbortSignal): Promise<HealthResponse> {
  const row = v.record(await apiFetch(`${BASE}/health`, { signal }))
  return {
    status: row.status === undefined ? undefined : v.string(row.status),
    backend: row.backend === undefined ? undefined : v.string(row.backend),
  }
}
