import { ref } from 'vue'
import { parseAceGenerationSettings, parseYueGenerationSettings } from '../api/contracts'
import type { AceGenerationSettings, YueGenerationSettings, GenerationPreset } from '../api/contracts'
import type { GenerationSnapshot } from '../api/generationLibrary'
export const pendingAceDraft = ref<AceGenerationSettings | null>(null)
export const pendingYueDraft = ref<YueGenerationSettings | null>(null)
export const editingGenerationPreset = ref<GenerationPreset | null>(null)
export function reuseGenerationSettings(settings: GenerationSnapshot, preset: GenerationPreset | null = null): void {
  const raw: unknown = JSON.parse(JSON.stringify(settings))
  const copied = settings.engine === 'ace_step' ? parseAceGenerationSettings(raw) : parseYueGenerationSettings(raw)
  if (copied.engine === 'ace_step') pendingAceDraft.value = copied
  else pendingYueDraft.value = copied
  editingGenerationPreset.value = preset ? { ...preset, settings: copied } : null
}
export const pendingAceReference = ref<(Partial<Pick<AceGenerationSettings, 'customLyrics'>> & Pick<AceGenerationSettings, 'referenceImportId'>) | null>(null)
export const pendingYueReference = ref<(Partial<Pick<YueGenerationSettings, 'lyrics' | 'abc'>> & Pick<YueGenerationSettings, 'referenceImportId'>) | null>(null)
export function applyReferenceText(engine: 'ace_step' | 'yue2', kind: 'lyrics' | 'abc', text: string, importId: string | null): void {
  if (!text.trim() || text.length > 100000 || importId !== null && !/^[a-f0-9]{32}$/.test(importId)) throw new TypeError('Invalid reference text')
  if (engine === 'ace_step' && kind === 'lyrics') pendingAceReference.value = { customLyrics: text, referenceImportId: importId }
  if (engine === 'yue2') pendingYueReference.value = kind === 'lyrics' ? { lyrics: text, referenceImportId: importId } : { abc: text, referenceImportId: importId }
}
