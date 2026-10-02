export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function parseErrorBody(resp: Response): Promise<string> {
  try {
    const data: unknown = await resp.clone().json()
    if (isObject(data)) {
      if (typeof data.error === 'string') return data.error
      if (typeof data.detail === 'string') return data.detail
      if (isObject(data.error) && typeof data.error.message === 'string') return data.error.message
    }
  } catch {
    // An unexpected response document must not become a debug dump in the UI.
  }
  return resp.statusText || `HTTP ${resp.status}`
}

export type Decoder<T> = (value: unknown) => T

export function apiFetch(url: string, init?: RequestInit): Promise<unknown>
export function apiFetch<T>(url: string, init: RequestInit | undefined, decode: Decoder<T>): Promise<T>
export async function apiFetch<T>(url: string, init?: RequestInit, decode?: Decoder<T>): Promise<unknown> {
  const resp = await fetch(url, init)
  if (!resp.ok) {
    throw new ApiError(await parseErrorBody(resp), resp.status)
  }
  const text = await resp.text()
  let data: unknown
  try {
    data = text ? JSON.parse(text) : undefined
  } catch {
    throw new ApiError('Invalid response from server', resp.status)
  }
  return decode ? decode(data) : data
}

export function apiJson(url: string, body: unknown, method?: string): Promise<unknown>
export function apiJson<T>(url: string, body: unknown, method: string | undefined, decode: Decoder<T>): Promise<T>
export function apiJson<T>(url: string, body: unknown, method = 'POST', decode?: Decoder<T>): Promise<unknown> {
  const init: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
  return decode ? apiFetch(url, init, decode) : apiFetch(url, init)
}

/** True when the request failed because the backing model process isn't running. */
export function isModelInactive(err: unknown): boolean {
  return err instanceof ApiError && err.status === 503
}
import { isObject } from './schemaValidation'
