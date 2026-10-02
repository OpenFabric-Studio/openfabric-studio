// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { rememberVoiceClock, rememberVoiceUsed, voiceClock, voiceUsed } from './voiceClock'

beforeEach(() => { localStorage.clear() })

it('rejects corrupted voice clocks and validates stored voice names', () => {
  localStorage.setItem('openfabric_voice_clock', JSON.stringify({ '1': { startedAt: 100, voiceId: { nested: true }, voiceName: 7 } }))
  localStorage.setItem('openfabric_voice_used', JSON.stringify({ '1': { voiceId: { nested: true }, voiceName: 7 } }))
  expect(voiceClock(1)).toBeNull()
  expect(voiceUsed(1)).toBeNull()
})

it('retains valid voice records when one stored row is corrupted', () => {
  localStorage.setItem('openfabric_voice_clock', JSON.stringify({ '1': null }))
  rememberVoiceClock(2, { startedAt: 100, voiceId: 'voice', voiceName: 'Name' })
  rememberVoiceUsed(2, 'voice', 'Name')
  expect(voiceClock(2)).toEqual({ startedAt: 100, voiceId: 'voice', voiceName: 'Name' })
  expect(voiceUsed(2)).toEqual({ voiceId: 'voice', voiceName: 'Name' })
})
