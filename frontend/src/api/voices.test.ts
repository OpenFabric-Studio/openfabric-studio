// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyStatus, waitForVoiceApply } from './voices'

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('voice observation cancellation', () => {
  it('forwards cancellation to the status request', async () => {
    const controller = new AbortController()
    const fetch = vi.fn().mockResolvedValue(new Response('{"status":"idle","error":"","error_code":"","audio_url":""}'))
    vi.stubGlobal('fetch', fetch)
    await applyStatus(1, controller.signal)
    expect(fetch).toHaveBeenCalledWith('/api/voices/apply/1', { signal: controller.signal })
  })

  it('aborts a polling delay immediately and clears its timer', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const fetch = vi.fn().mockResolvedValue(new Response('{"status":"running","error":"","error_code":"","audio_url":""}'))
    vi.stubGlobal('fetch', fetch)
    const waiting = waitForVoiceApply(1, undefined, controller.signal).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(1)
    controller.abort()
    await vi.advanceTimersByTimeAsync(0)
    expect(vi.getTimerCount()).toBe(0)
    expect(await waiting).toMatchObject({ name: 'AbortError' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('does not deliver a late completed response after cancellation', async () => {
    const controller = new AbortController()
    let complete: (response: Response) => void = () => { throw new Error('Request not started') }
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { complete = resolve })))
    const update = vi.fn()
    const waiting = waitForVoiceApply(1, update, controller.signal).catch((error: unknown) => error)
    controller.abort()
    complete(new Response('{"status":"done","error":"","error_code":"","audio_url":"/audio"}'))
    expect(await waiting).toMatchObject({ name: 'AbortError' })
    expect(update).not.toHaveBeenCalled()
  })
})
