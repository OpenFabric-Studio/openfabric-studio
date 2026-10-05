import { ApiError, apiFetch } from './http'
import { parseLocalEnginesStatus, parseLocalEngineResponse, parseLocalInputResponse, parseKokoroRequest, parseChatterboxRequest, parseWanRequest, parseRvcRequest } from './contracts'
import type { LocalEngineStatus, LocalEnginesStatus, LocalEngineResponse, KokoroRequest, ChatterboxRequest, WanRequest, RvcRequest } from './contracts'
export type { LocalEnginesStatus } from './contracts'

export type LocalEngineId = LocalEngineStatus['id']
export type LocalEngineCard = LocalEngineStatus
export type LocalEngineResult = LocalEngineResponse

export const decodeLocalEnginesStatus = parseLocalEnginesStatus
export const decodeLocalEngineResult = parseLocalEngineResponse

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

export async function runKokoro(body: KokoroRequest, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/kokoro', parseKokoroRequest(body), signal)
}

export async function runChatterbox(body: ChatterboxRequest, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/chatterbox', parseChatterboxRequest(body), signal)
}

export async function runWan(body: Pick<WanRequest, 'prompt' | 'image_path'>, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/wan', parseWanRequest({ engine: 'wan22', variant: 'ti2v-5b', prompt: body.prompt, ...(body.image_path ? { image_path: body.image_path } : {}) }), signal)
}

export async function runRvc(body: RvcRequest, signal?: AbortSignal): Promise<LocalEngineResult> {
  return postEngine('/api/local-engines/rvc', parseRvcRequest(body), signal)
}

export async function uploadLocalInput(file: File, signal?: AbortSignal): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  const value = await apiFetch('/api/local-engines/inputs', { method: 'POST', body: form, signal }, parseLocalInputResponse)
  return value.path
}
