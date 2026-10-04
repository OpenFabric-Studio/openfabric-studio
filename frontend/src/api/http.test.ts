import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, apiJson } from './http'

function countResponse(value: unknown): { count: number } {
  if (typeof value !== 'object' || value === null || !('count' in value)
    || typeof value.count !== 'number' || !Number.isFinite(value.count)) {
    throw new TypeError('Invalid count response')
  }
  return { count: value.count }
}

afterEach(() => vi.unstubAllGlobals())

describe('HTTP response boundaries', () => {
  it('runs the requested decoder and rejects a malformed successful response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"count":"broken"}')))
    await expect(apiFetch('/example', undefined, countResponse)).rejects.toThrow('Invalid count response')
  })

  it('uses the same decoder on JSON mutation responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"count":"broken"}')))
    await expect(apiJson('/example', { name: 'test' }, 'PUT', countResponse)).rejects.toThrow('Invalid count response')
  })

  it('returns a validated response without coercing numeric strings', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"count":2}')))
    await expect(apiFetch('/example', undefined, countResponse)).resolves.toEqual({ count: 2 })
  })

  it('reports malformed JSON without displaying response contents', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"secret":sensitive}')))
    await expect(apiFetch('/example')).rejects.toThrow('Invalid response from server')
  })

  it('does not stringify an unexpected error document into a user message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"debug":"private path"}', { status: 500 })))
    await expect(apiFetch('/example')).rejects.toEqual(new ApiError('HTTP 500', 500))
  })

  it('shows a nested engine detail without a stack trace', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: 'weights_missing', detail: 'Place these files yourself. OpenFabric does not download them for kokoro: kokoro-v1_0.pth, voices/af_heart.pt.' } }), { status: 409 })))
    await expect(apiFetch('/example')).rejects.toEqual(new ApiError('Place these files yourself. OpenFabric does not download them for kokoro: kokoro-v1_0.pth, voices/af_heart.pt.', 409))
  })
})
