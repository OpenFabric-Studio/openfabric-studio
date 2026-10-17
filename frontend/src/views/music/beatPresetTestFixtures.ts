import definitions from '../../../../backend/app/data/beat_presets.json'
import scales from '../../../../backend/app/data/beat_scales.json'
import tunings from '../../../../backend/app/data/beat_tunings.json'
import rhythms from '../../../../backend/app/data/beat_rhythms.json'
import { parseBeatPresetCatalog } from '../../api/generated'

export function presetCatalog(available = false) {
  return parseBeatPresetCatalog({ version: definitions.version,
    presets: definitions.presets.map((preset, index) => ({ preset, preset_sha256: 'c'.repeat(64), availability: available && index === 0 ? 'available' : 'pack_missing', compatible_kit_ids: available && index === 0 ? ['test-kit'] : [], missing_instrument_ids: [] })),
    scales: scales.scales, tunings: tunings.tunings, rhythms: rhythms.rhythms })
}
