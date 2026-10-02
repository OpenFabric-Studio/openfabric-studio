// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import * as api from './voices'
import type { SavedTrack } from './contracts'

const voiceId = 'a'.repeat(32)
const source: SavedTrack = { id: 40, short_id: 10, model: 'upload', created_at: '2026-10-01T10:00:00Z', title: 'Original', lyrics: '', seed: null,
  duration_ms: 30_000, wall_ms: null, params: { source: 'voice_replacement_source' }, filename: 'original.wav', audio_url: '/api/tracks/40/audio', abc_url: null, stems: null, midi: null }
const result: SavedTrack = { ...source, id: 41, short_id: 11, title: 'Replacement', params: { source: 'voice_replacement', source_track_id: 40, voice_id: voiceId, audio_format: 'wav' } }
const application = { status: 'queued', error: '', error_code: '', audio_url: '', voice_id: voiceId }

afterEach(() => { vi.unstubAllGlobals() })

it('uploads multipart replacement audio and captured voice with cancellation and validates the response', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ track: result, source_track: source, application })))
  vi.stubGlobal('fetch', fetch)
  const audio = new File(['original audio'], 'song.wav', { type: 'audio/wav' })
  const controller = new AbortController()
  expect(await api.replaceVoice(audio, voiceId, controller.signal)).toMatchObject({ track: { id: 41 }, source_track: { id: 40 }, application })
  const [url, init] = fetch.mock.calls[0] ?? []
  expect(url).toBe('/api/voices/replace')
  expect(init?.signal).toBe(controller.signal)
  expect(init?.method).toBe('POST')
  expect(init?.body).toBeInstanceOf(FormData)
  if (!(init?.body instanceof FormData)) throw new Error('Missing multipart request')
  expect(init.body.get('voice_id')).toBe(voiceId)
  expect(init.body.get('audio')).toBeInstanceOf(File)
  expect(init.headers).toBeUndefined()
})

it('rejects invalid voice IDs before uploading any audio', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch)
  await expect(api.replaceVoice(new File(['audio'], 'song.wav'), '../voice')).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('rejects malformed replacement tracks at the HTTP boundary', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ track: { ...result, id: 'unsafe' }, source_track: source, application }))))
  await expect(api.replaceVoice(new File(['audio'], 'song.wav'), voiceId)).rejects.toThrow()
})

it('requests drained apply cancellation and rejects invalid track IDs before fetch', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ ...application, status: 'cancelled', error_code: 'cancelled' })))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.cancelVoiceApply(41, controller.signal)).toMatchObject({ status: 'cancelled' })
  expect(fetch).toHaveBeenCalledWith('/api/voices/apply/41/cancel', { method: 'POST', signal: controller.signal })
  await expect(api.cancelVoiceApply(-1)).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('retries conversion through the typed apply API with the captured voice', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(application)))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.applyVoice(voiceId, 41, controller.signal)).toMatchObject(application)
  expect(fetch).toHaveBeenCalledWith('/api/voices/apply', expect.objectContaining({ method: 'POST', signal: controller.signal,
    body: JSON.stringify({ voice_id: voiceId, track_id: 41 }) }))
})
