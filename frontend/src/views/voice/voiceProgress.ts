import type { VoiceJobProgress, VoicePreparationResponse, VoiceProfileResponse } from '../../api/contracts'
import type { VoiceReviewState, VoiceWorkspaceStep } from './voiceWorkspace'

/** Total wall time includes queueing; terminal timestamps freeze after reload. */
export function voiceElapsedSeconds(progress: VoiceJobProgress | null | undefined, now: number): number | null {
  if (!progress) return null
  if (progress.status !== 'queued' && progress.status !== 'running' && progress.finished_at == null) return null
  return Math.max(0, (progress.finished_at ?? now) - progress.queued_at)
}

export function voicePhaseRemainingSeconds(progress: VoiceJobProgress | null | undefined, now: number): number | null {
  if (!progress || progress.status !== 'running' || progress.estimated_phase_remaining_sec == null) return null
  const age = Math.max(0, now - progress.observed_at)
  // Once an observation's estimate expires, wait for new measured progress
  // rather than claiming the stage is complete or leaving a zero countdown.
  const remaining = progress.estimated_phase_remaining_sec - age
  return remaining > 0 ? remaining : null
}

export function formatVoiceTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor(whole % 3600 / 60)
  const remainder = whole % 60
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}` : `${minutes}:${String(remainder).padStart(2, '0')}`
}

interface StepFact { complete: boolean; available: boolean; prerequisite: VoiceWorkspaceStep | null }
export function voiceStepFacts(voice: VoiceProfileResponse | null, preparation: VoicePreparationResponse | null, state: VoiceReviewState): Record<VoiceWorkspaceStep, StepFact> {
  const filesDone = preparation?.status === 'done' && !!preparation.segments?.length && !state.optionsDirty
  const samplesDone = filesDone && state.validSelection && state.referenceReady && !state.selectionDirty
  const coverageDone = samplesDone && !!preparation?.coverage && !preparation.coverage.unavailable_segments
  const buildDone = samplesDone && !!voice?.usable && voice.job_progress?.status === 'done' && voice.job_progress.preparation_revision === preparation?.revision
  return {
    files: { complete: filesDone, available: true, prerequisite: null },
    samples: { complete: samplesDone, available: filesDone || !!preparation?.segments?.length, prerequisite: 'files' },
    coverage: { complete: coverageDone, available: samplesDone, prerequisite: 'samples' },
    build: { complete: buildDone, available: samplesDone, prerequisite: 'samples' },
    // Comparing is optional, subjective review. Existing results are not proof
    // that this revision or its currently selected model has been evaluated.
    compare: { complete: false, available: samplesDone && !!voice?.usable, prerequisite: voice?.usable ? 'samples' : 'build' },
  }
}
