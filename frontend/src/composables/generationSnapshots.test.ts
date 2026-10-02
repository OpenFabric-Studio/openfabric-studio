import { expect, it } from 'vitest'
import { legacyPresetImports, emptyAceSettings, emptyYueSettings } from './generationSnapshots'
it('migrates validated legacy presets with stable per-engine keys and complete defaults', () => {
  const ace = JSON.stringify([{ name: 'Jazz', mode: 'custom', audioFormat: 'wav', customLyrics: 'Lyrics', customPrompt: 'jazz', inferenceSteps: null }])
  const yue = JSON.stringify([{ name: 'Jazz', cot: 'off', precision: 'q8_0', lyrics: 'Other lyrics', numInferenceSteps: null }])
  const migrated = legacyPresetImports(ace, yue)
  expect(migrated).toHaveLength(2)
  expect(migrated[0]).toMatchObject({ legacy_key: expect.stringMatching(/^acestep_presets:Jazz:[a-f0-9]{16}$/), name: 'Jazz', settings: { engine: 'ace_step', customLyrics: 'Lyrics', seed: null, inferenceSteps: null } })
  expect(migrated[1]).toMatchObject({ legacy_key: expect.stringMatching(/^yue2_presets:Jazz:[a-f0-9]{16}$/), settings: { engine: 'yue2', numInferenceSteps: null, randomSeed: false } })
  expect(legacyPresetImports(ace, yue)).toEqual(migrated)
})
it('preserves valid presets around malformed storage and rejects out-of-bounds settings', () => {
  expect(legacyPresetImports('broken', JSON.stringify([{ name: 'valid', cot: 'off', precision: 'q8_0' }, { name: 'bad', cot: 'off', precision: 'q8_0', cfgScale: 10001 }]))).toHaveLength(1)
})
it('constructs schema-complete empty form snapshots without shared sampling objects', () => {
  const first = emptyYueSettings(); first.semantic.temperature = 0.5
  expect(emptyYueSettings().semantic.temperature).toBeNull()
  expect(emptyYueSettings().numInferenceSteps).toBe(8)
  expect(emptyAceSettings().useCotCaption).toBe(true)
})
it('reuses each completed batch result seed and generated lyrics without mutating input settings', async () => {
  const { historyResultSettings } = await import('./generationSnapshots')
  const input = { ...emptyAceSettings(), simpleQuery: 'jazz ballad', customLyrics: '', randomSeed: true, seed: 11, batchSize: 4 }
  const entry = { id: 'a'.repeat(32), engine: 'ace_step' as const, settings: input, lyrics: '[Verse]\nActual generated lyrics', seed: 88, title: 'Song', created_at: '2026-10-01', track_id: 7, reference_requires_reupload: false }
  expect(historyResultSettings(entry)).toMatchObject({ engine: 'ace_step', mode: 'custom', customPrompt: 'jazz ballad', customLyrics: entry.lyrics, seed: 88, randomSeed: false, batchSize: 4 })
  expect(historyResultSettings({ ...entry, seed: 89 })).toMatchObject({ seed: 89, randomSeed: false })
  expect(input).toMatchObject({ mode: 'simple', customLyrics: '', randomSeed: true, seed: 11 })
})
it('keeps instrumental simple mode and absent result seed intention intact', async () => {
  const { historyResultSettings } = await import('./generationSnapshots')
  const settings = { ...emptyAceSettings(), simpleQuery: 'instrumental jazz', instrumental: true }
  expect(historyResultSettings({ id: 'a'.repeat(32), engine: 'ace_step', settings, lyrics: '', seed: null, title: 'Song', created_at: '2026-10-01', track_id: null, reference_requires_reupload: false })).toMatchObject({ mode: 'simple', instrumental: true, randomSeed: true, seed: null })
})
it('uses actual YuE lyrics and safe seed while retaining the rest of the input settings', async () => {
  const { historyResultSettings } = await import('./generationSnapshots')
  const entry = { id: 'a'.repeat(32), engine: 'yue2' as const, settings: { ...emptyYueSettings(), lyrics: 'Input lyrics', randomSeed: true, seed: 11, batchSize: 3 }, lyrics: 'Result lyrics', seed: 55, title: 'Song', created_at: '2026-10-01', track_id: 7, reference_requires_reupload: false }
  expect(historyResultSettings(entry)).toMatchObject({ lyrics: 'Result lyrics', seed: 55, randomSeed: false, batchSize: 3 })
  expect(historyResultSettings({ ...entry, seed: -1 })).toMatchObject({ seed: 11, randomSeed: true })
})
