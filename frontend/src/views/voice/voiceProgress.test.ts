import { expect, it } from 'vitest'
import type { VoiceJobProgress, VoiceProfileResponse } from '../../api/contracts'
import { voiceElapsedSeconds, voicePhaseRemainingSeconds, voiceStepFacts } from './voiceProgress'
import { preparedVoice, voiceProfile } from './voiceTestFixtures'

const progress: VoiceJobProgress = { job_id: 'job', kind: 'build', status: 'running', preparation_revision: 'revision-1', queued_at: 10, started_at: 20, phase_started_at: 25, observed_at: 30, phase: 'training', phase_current: 3, phase_total: 200, phase_unit: 'steps', estimated_phase_remaining_sec: 394 }
it('uses persisted start/finish times and does not invent a legacy clock', () => {
  expect(voiceElapsedSeconds(null, 50)).toBeNull()
  expect(voiceElapsedSeconds(progress, 50)).toBe(40)
  expect(voiceElapsedSeconds({ ...progress, status: 'cancelled', finished_at: 40 }, 999)).toBe(30)
  expect(voiceElapsedSeconds({ ...progress, started_at: null, finished_at: 40 }, 999)).toBe(30)
  expect(voiceElapsedSeconds({ ...progress, status: 'done', finished_at: null }, 999)).toBeNull()
})
it('only shows a current-phase measured estimate while it is still useful', () => {
  expect(voicePhaseRemainingSeconds({ ...progress, estimated_phase_remaining_sec: null }, 50)).toBeNull()
  expect(voicePhaseRemainingSeconds(progress, 34)).toBe(390)
  expect(voicePhaseRemainingSeconds(progress, 999)).toBeNull()
  expect(voicePhaseRemainingSeconds({ ...progress, status: 'done', finished_at: 40 }, 50)).toBeNull()
  expect(voicePhaseRemainingSeconds({ ...progress, estimated_phase_remaining_sec: 30 }, 61)).toBeNull()
  expect(voicePhaseRemainingSeconds({ ...progress, status: 'queued' }, 34)).toBeNull()
})
it('invalidates downstream completion when the saved revision or draft changes', () => {
  const voice: VoiceProfileResponse = { ...voiceProfile(), status: 'ready', usable: true, job_progress: { ...progress, status: 'done', finished_at: 40 } }
  const state = { loading: false, preparing: false, optionsDirty: false, selectionDirty: false, selectedCount: 1, referenceReady: true, validSelection: true, canBuild: true, canPrepare: true, canAnalyzeCoverage: true, action: '', error: '' }
  const preparation = preparedVoice()
  expect(voiceStepFacts(voice, preparation, state).build.complete).toBe(true)
  expect(voiceStepFacts(voice, { ...preparation, revision: 'revision-2' }, state).build.complete).toBe(false)
  const changed = voiceStepFacts(voice, preparation, { ...state, selectionDirty: true })
  expect(changed.samples.complete).toBe(false)
  expect(changed.coverage.available).toBe(false)
  expect(changed.build.complete).toBe(false)
  expect(changed.compare.available).toBe(false)
})
