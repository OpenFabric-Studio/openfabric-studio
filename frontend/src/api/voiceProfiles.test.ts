// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import * as api from './voiceProfiles'
import type { StarterSpeechVoice } from './contracts'

const voice: StarterSpeechVoice = { id: 'vctk-p225', name: 'VCTK p225', accent: 'English · Southern England', transcript: 'Please call Stella.', duration_seconds: 4.2, sample_rate_hz: 48000, audio_url: '/api/voice-profiles/starter-voices/vctk-p225/audio', source_url: 'https://datashare.ed.ac.uk/handle/10283/3443', license_name: 'CC BY 4.0', license_url: 'https://creativecommons.org/licenses/by/4.0/', attribution: 'VCTK Corpus 0.92 · University of Edinburgh' }
afterEach(() => vi.unstubAllGlobals())
it('loads the schema-validated starter catalog and forwards cancellation', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ voices: [voice] })))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.listStarterSpeechVoices(controller.signal)).toEqual([voice])
  expect(fetch).toHaveBeenCalledWith('/api/voice-profiles/starter-voices', { signal: controller.signal })
})
it.each([{ audio_url: 'https://external.invalid/audio' }, { duration_seconds: '4' }, { license_url: 'https://external.invalid/license' }])('rejects malformed catalog metadata at the HTTP boundary', async invalid => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ voices: [{ ...voice, ...invalid }] }))))
  await expect(api.listStarterSpeechVoices()).rejects.toThrow('Invalid StarterSpeechVoicesResponse')
})
it('imports a starter through its identifier and validates the resulting profile', async () => {
  const profile = { id: 'a'.repeat(32), name: voice.name, consent_confirmed: true, reference_audio_path: '/library/reference.wav', notes: voice.transcript, created_at: 'now', updated_at: 'now', starter_voice_id: voice.id }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(profile)))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.importStarterSpeechVoice(voice.id, controller.signal)).toEqual(profile)
  expect(fetch).toHaveBeenCalledWith(`/api/voice-profiles/starter-voices/${voice.id}/import`, { method: 'POST', signal: controller.signal })
})
it('rejects an unsafe import identifier without sending a request', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  await expect(api.importStarterSpeechVoice('../secret')).rejects.toThrow('Invalid starter voice identifier')
  expect(fetch).not.toHaveBeenCalled()
})
it.each(['../secret', 'trial-1', 'A'.repeat(32), 'a'.repeat(31), '', null, undefined])('rejects unsafe trial media identifiers: %s', id => {
  expect(api.speechTrialAudioUrl(id)).toBeNull()
})
it('constructs the trial media URL from a valid identifier without using an output path', () => {
  const id = 'b'.repeat(32)
  expect(api.speechTrialAudioUrl(id)).toBe(`/api/speech-clone/trials/${id}/audio`)
})
