import type { LocationQueryValue } from 'vue-router'
import type { VoicePreparationResponse, VoiceProfileResponse } from '../../api/contracts'
import { isVoiceActive } from '../../api/voices'
import { voiceWorkspaceSteps, type VoiceWorkspaceStep } from './voiceWorkspace'
export type SingingStage = 'overview' | VoiceWorkspaceStep
export function singingStage(value: LocationQueryValue | LocationQueryValue[] | undefined): SingingStage | null {
  if (value === 'overview') return value
  return voiceWorkspaceSteps.find(step => step === value) ?? null
}
export function singingInitialStage(voice: VoiceProfileResponse | null): SingingStage {
  if (isVoiceActive(voice?.status) || voice?.status === 'failed' || voice?.status === 'cancelled') return 'build'
  return voice?.usable ? 'overview' : 'files'
}
/** Compare published inputs with saved source choices; absent legacy provenance is unknown. */
export function publishedSourcesChanged(voice: VoiceProfileResponse | null, preparation: VoicePreparationResponse | null): boolean | null {
  if (!voice?.usable || !voice.built_from.length) return null
  const choices = preparation?.options?.sources
  if (!choices?.length) return null
  const current = new Set(voice.recordings.filter(recording => choices.find(source => source.filename === recording.filename)?.enabled !== false).map(recording => recording.filename))
  const built = new Set(voice.built_from)
  return current.size !== built.size || [...built].some(filename => !current.has(filename))
}
