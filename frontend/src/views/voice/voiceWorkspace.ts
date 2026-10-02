import type { VoiceSegment } from '../../api/contracts'

export const voiceWorkspaceSteps = ['files', 'samples', 'coverage', 'build', 'compare'] as const
export type VoiceWorkspaceStep = typeof voiceWorkspaceSteps[number]

/** Mirrors the engine's supported training clip duration; the backend validates selection. */
export function isSelectableVoiceSample(segment: VoiceSegment): boolean {
  return segment.accepted && segment.duration_sec >= 1 && segment.duration_sec <= 30
}

export interface VoiceReviewState {
  loading: boolean
  preparing: boolean
  optionsDirty: boolean
  selectionDirty: boolean
  selectedCount: number
  referenceReady: boolean
  validSelection: boolean
  canBuild: boolean
  canPrepare: boolean
  canAnalyzeCoverage: boolean
  action: string
  error: string
}

export function emptyVoiceReviewState(): VoiceReviewState {
  return { loading: true, preparing: false, optionsDirty: false, selectionDirty: false, selectedCount: 0, referenceReady: false, validSelection: false, canBuild: false, canPrepare: false, canAnalyzeCoverage: false, action: '', error: '' }
}
