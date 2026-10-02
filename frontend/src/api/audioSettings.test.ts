import { afterEach, expect, it, vi } from 'vitest'
import * as api from './audioSettings'
import { audioSettingsResponse, encodingSettings } from '../views/settings/settingsTestFixtures'

afterEach(() => vi.unstubAllGlobals())

it('loads validated settings and server defaults with the request signal', async () => {
  const response = audioSettingsResponse()
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(response)))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.getAudioSettings(controller.signal)).toEqual(response)
  expect(fetch).toHaveBeenCalledWith('/api/settings/audio', { signal: controller.signal })
})

it('saves a validated direct encoding settings document with cancellation', async () => {
  const response = audioSettingsResponse()
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(response)))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.saveAudioSettings(response.settings, controller.signal)).toEqual(response)
  expect(fetch).toHaveBeenCalledWith('/api/settings/audio', { method: 'PUT', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(response.settings) })
})

it('rejects invalid request values before any settings write', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch)
  const settings = encodingSettings(); settings.mp3.vbr_quality = 10
  await expect(api.saveAudioSettings(settings)).rejects.toBeInstanceOf(TypeError)
  expect(fetch).not.toHaveBeenCalled()
})

it.each([
  { settings: { ...encodingSettings(), mp3: { ...encodingSettings().mp3, mode: 'unknown' } }, defaults: encodingSettings() },
  { settings: encodingSettings(), defaults: { ...encodingSettings(), wav: { ...encodingSettings().wav, bit_depth: 20 } } },
  { settings: { ...encodingSettings(), flac: { ...encodingSettings().flac, compression_level: 9 } }, defaults: encodingSettings() },
  { settings: {}, defaults: encodingSettings() },
  { settings: encodingSettings(), defaults: {} },
  { settings: { ...encodingSettings(), mp3: {} }, defaults: encodingSettings() },
])('rejects malformed settings or defaults at the HTTP boundary', async (value) => {
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(value))))
  await expect(api.getAudioSettings()).rejects.toBeInstanceOf(TypeError)
})

it('keeps structured backend failures available to the UI', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ error: 'settings_write_failed' }), { status: 500 })))
  await expect(api.saveAudioSettings(encodingSettings())).rejects.toMatchObject({ name: 'ApiError', message: 'settings_write_failed', status: 500 })
})
