import { apiFetch } from './http'
import { parseRvcModelImport, parseRvcModelsResponse, parseRvcRegisteredModel } from './generated'
const base = '/api/voices/replace/rvc-models'
const maximum = 256 * 1024 ** 2
export const listRvcModels = (signal?: AbortSignal) => apiFetch(base, { signal }, parseRvcModelsResponse)
export function importRvcModel(metadata: unknown, checkpoint: File, index?: File, signal?: AbortSignal) {
  const request = parseRvcModelImport(metadata), encoded = JSON.stringify(request)
  if (!checkpoint.name.toLowerCase().endsWith('.pth') || checkpoint.size <= 0 || index && (!index.name.toLowerCase().endsWith('.index') || index.size <= 0) || checkpoint.size + (index?.size ?? 0) > maximum || new TextEncoder().encode(encoded).byteLength > 65536 || (request.index_rate ?? 0) > 0 && !index) throw new TypeError('Invalid bounded RVC import')
  const form = new FormData(); form.append('metadata', encoded); form.append('checkpoint', checkpoint, checkpoint.name); if (index) form.append('index', index, index.name)
  return apiFetch(base, { method: 'POST', signal, body: form }, value => { const result = parseRvcRegisteredModel(value); if (result.voice_id !== request.voice_id || (result.request_id ?? null) !== (request.request_id ?? null)) throw new TypeError('Mismatched RVC model'); return result })
}
export async function removeRvcModel(id: string, signal?: AbortSignal): Promise<void> { if (!/^[a-f0-9]{32}$/.test(id)) throw new TypeError('Invalid RVC identity'); await apiFetch(`${base}/${id}`, { method: 'DELETE', signal }) }
