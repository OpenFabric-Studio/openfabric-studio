import type { AceGenerationSettings, YueGenerationSettings, ImportGenerationPreset, GenerationHistoryEntry } from '../api/contracts'
import { parseAceGenerationSettings, parseYueGenerationSettings } from '../api/contracts'
import { parseAcePresets, parseYue2Presets, samplingSettings } from './generationPresets'
export function emptyAceSettings(): AceGenerationSettings {
  return { engine: 'ace_step', referenceImportId: null, mode: 'simple', simpleQuery: '', customPrompt: '', instrumental: false,
    customLyrics: '', duration: 120, durationAuto: true, audioFormat: 'mp3', bpm: null, keyScale: '',
    timeSignature: '', vocalLanguage: '', inferenceSteps: null, guidanceScale: null, selectedModel: '',
    seed: null, randomSeed: true, batchSize: 1, useRefAudio: false, taskType: 'text2music',
    repaintStart: null, repaintEnd: null, trackName: 'vocals', trackClasses: [], coverStrength: 1,
    voiceId: null, useCotCaption: true, styleReferenceRequiresReupload: false, sourceReferenceName: null,
    styleReferenceName: null, loraRequiresReselection: false, loraName: null, loraScale: 1 }
}
export function emptyYueSettings(): YueGenerationSettings {
  return { engine: 'yue2', referenceImportId: null, lyrics: '', style: '', cot: 'off', precision: 'q8_0', abc: '',
    cfgScale: null, numInferenceSteps: 8, semantic: samplingSettings(null), abcSampling: samplingSettings(null),
    seed: 831001, randomSeed: false, batchSize: 1, voiceId: null, referenceRequiresReupload: false }
}
// Stable content fingerprint for migration identity, not a security checksum.
function fingerprint(settings: object): string {
  let hash = 14695981039346656037n
  for (const char of JSON.stringify(settings)) { hash ^= BigInt(char.charCodeAt(0)); hash = BigInt.asUintN(64, hash * 1099511628211n) }
  return hash.toString(16).padStart(16, '0')
}
function json(raw: string | null): unknown { try { return raw ? JSON.parse(raw) : null } catch { return null } }
export function legacyPresetImports(aceRaw: string | null, yueRaw: string | null): ImportGenerationPreset[] {
  const imports: ImportGenerationPreset[] = []
  for (const preset of parseAcePresets(json(aceRaw))) {
    const { name, ...values } = preset
    try { const settings = parseAceGenerationSettings({ ...emptyAceSettings(), ...values }); imports.push({ legacy_key: `acestep_presets:${name}:${fingerprint(settings)}`, name, source_history_id: null, settings }) } catch { /* Preserve invalid legacy records in localStorage. */ }
  }
  for (const preset of parseYue2Presets(json(yueRaw))) {
    const { name, ...values } = preset
    try { const settings = parseYueGenerationSettings({ ...emptyYueSettings(), ...values }); imports.push({ legacy_key: `yue2_presets:${name}:${fingerprint(settings)}`, name, source_history_id: null, settings }) } catch { /* Preserve invalid legacy records in localStorage. */ }
  }
  return imports.filter(preset => preset.name.trim().length <= 200 && preset.legacy_key.length <= 500)
}

/** Reuse a completed output while retaining its original input snapshot. */
export function historyResultSettings(entry: GenerationHistoryEntry): GenerationHistoryEntry['settings'] {
  const settings = entry.settings
  const seedKnown = entry.seed !== null && Number.isSafeInteger(entry.seed) && entry.seed >= 0
  const seed = seedKnown ? entry.seed : settings.seed
  const randomSeed = seedKnown ? false : settings.randomSeed
  if (settings.engine === 'yue2') return { ...settings, lyrics: entry.lyrics, seed, randomSeed }
  const generatedVocals = !settings.instrumental && !!entry.lyrics.trim()
  return { ...settings, seed, randomSeed, customLyrics: entry.lyrics,
    mode: settings.mode === 'simple' && generatedVocals ? 'custom' : settings.mode,
    customPrompt: settings.mode === 'simple' && generatedVocals ? settings.customPrompt || settings.simpleQuery : settings.customPrompt }
}
