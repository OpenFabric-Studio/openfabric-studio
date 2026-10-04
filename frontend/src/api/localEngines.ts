import { ApiError, apiFetch } from './http'
import { isObject } from './schemaValidation'

export type LocalEngineId = 'kokoro' | 'chatterbox' | 'wan22' | 'rvc'

export interface LocalEngineCard {
  id: LocalEngineId
  installed: boolean
  setup_script: string
  runtime: string
  voices: string[]
  languages: string[]
}

export interface LocalEnginesStatus {
  video_engine: 'ltx'
  video_preference: string
  note: string
  engines: LocalEngineCard[]
}

export interface LocalEngineResult {
  status: string
  detail: string
  output_path: string | null
  media_url: string | null
  runtime: string
}

const engineIds: readonly LocalEngineId[] = ['kokoro', 'chatterbox', 'wan22', 'rvc']

function stringList(value: unknown): string[] {
  if (!Array.isArray(value) || !value.every(item => typeof item === 'string')) throw new TypeError('Invalid local engine response')
  return value
}

function decodeCard(value: unknown): LocalEngineCard {
  if (!isObject(value) || !engineIds.includes(value.id as LocalEngineId) || typeof value.installed !== 'boolean'
    || typeof value.setup_script !== 'string' || typeof value.runtime !== 'string') {
    throw new TypeError('Invalid local engine response')
  }
  return {
    id: value.id as LocalEngineId,
    installed: value.installed,
    setup_script: value.setup_script,
    runtime: value.runtime,
    voices: stringList(value.voices ?? []),
    languages: stringList(value.languages ?? []),
  }
}

export function decodeLocalEnginesStatus(value: unknown): LocalEnginesStatus {
  if (!isObject(value) || value.video_engine !== 'ltx' || typeof value.video_preference !== 'string'
    || typeof value.note !== 'string' || !Array.isArray(value.engines)) {
    throw new TypeError('Invalid local engine response')
  }
  return { video_engine: 'ltx', video_preference: value.video_preference, note: value.note, engines: value.engines.map(decodeCard) }
}

export function decodeLocalEngineResult(value: unknown): LocalEngineResult {
  if (!isObject(value) || typeof value.status !== 'string' || typeof value.detail !== 'string'
    || (value.output_path !== null && typeof value.output_path !== 'string')
    || (value.media_url !== null && value.media_url !== undefined && typeof value.media_url !== 'string')
    || typeof value.runtime !== 'string') {
    throw new TypeError('Invalid local engine response')
  }
  return {
    status: value.status,
    detail: value.detail,
    output_path: typeof value.output_path === 'string' ? value.output_path : null,
    media_url: typeof value.media_url === 'string' ? value.media_url : null,
    runtime: value.runtime,
  }
}

/** Play a local-engine file through the same /api media path other players use. */
export function localEngineMediaUrl(result: Pick<LocalEngineResult, 'media_url' | 'output_path'>): string | null {
  if (result.media_url?.startsWith('/api/local-engines/')) return result.media_url
  const path = result.output_path?.replaceAll('\\', '/') ?? ''
  const match = path.match(/local-engines\/(kokoro|chatterbox|wan22|rvc)\/([0-9a-f]{32})\.(wav|mp4)$/i)
  return match ? `/api/local-engines/${match[1]}/media/${match[2]}` : null
}

export function localEngineError(err: unknown): string {
  return err instanceof ApiError && err.message ? err.message : ''
}

export function listLocalEngines(signal?: AbortSignal): Promise<LocalEnginesStatus> {
  return apiFetch('/api/local-engines', { signal }, decodeLocalEnginesStatus)
}

function postEngine(url: string, body: unknown, signal?: AbortSignal): Promise<LocalEngineResult> {
  return apiFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }, decodeLocalEngineResult)
}

export function runKokoro(body: { text: string; voice: string; lang: 'a' | 'b' }, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/kokoro', body, signal)
}

export function runChatterbox(body: { text: string; model: 'original' | 'multilingual'; audio_prompt_path?: string; language_id?: string }, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/chatterbox', body, signal)
}

export function runWan(body: { prompt: string; image_path?: string }, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/wan', { engine: 'wan22', variant: 'ti2v-5b', prompt: body.prompt, ...(body.image_path ? { image_path: body.image_path } : {}) }, signal)
}

export function runRvc(body: { model_path: string; input_path: string }, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/rvc', body, signal)
}

export async function uploadLocalInput(file: File, signal?: AbortSignal): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  const value = await apiFetch('/api/local-engines/inputs', { method: 'POST', body: form, signal }, (data: unknown) => {
    if (!isObject(data) || typeof data.path !== 'string' || !data.path) throw new TypeError('Invalid local engine response')
    return data.path
  })
  return value
}
