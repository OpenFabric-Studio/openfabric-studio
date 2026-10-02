import { expect, it } from 'vitest'
import { parseAcePresets, parseYue2Presets, samplingSettings } from './generationPresets'

it('rejects malformed presets and unsupported enum values', () => {
  expect(parseAcePresets({ name: 'not an array' })).toEqual([])
  expect(parseAcePresets([null, { name: 'bad', mode: 'other', audioFormat: 'mp3' }])).toEqual([])
  expect(parseYue2Presets([{ name: 'bad', cot: 'other', precision: 'q8_0' }])).toEqual([])
})

it('keeps supported settings while constraining stored ACE durations', () => {
  const presets = parseAcePresets([{ name: 'Valid', mode: 'simple', audioFormat: 'wav', duration: 10_000, instrumental: true, bpm: 'fast' }])
  expect(presets[0]).toMatchObject({ name: 'Valid', duration: 300, bpm: null, instrumental: true })
})

it('copies only supported numeric sampling keys', () => {
  expect(samplingSettings({ temperature: 0.5, top_p: 'one', extra: 'ignored' })).toEqual({ temperature: 0.5, top_p: null, top_k: null, repetition_penalty: null, penalty_window: null, min_tokens: null, max_tokens: null })
})
