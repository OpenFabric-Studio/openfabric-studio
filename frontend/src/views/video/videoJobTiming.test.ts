import { expect, it } from 'vitest'
import { notePhasePace, phaseRemaining } from './videoJobTiming'
import type { VideoProjectJob } from '../../api/contracts'
const job: VideoProjectJob = { id: 'a'.repeat(32), operation: 'preview', status: 'running', phase: 'denoise', shot_index: 1, progress_current: 1, progress_total: 10 }
it('does not invent an ETA from startup or a single measured delta', () => {
  const first = notePhasePace(undefined, job, 0)
  expect(phaseRemaining(first, job, 0)).toBeNull()
  const second = notePhasePace(first, { ...job, progress_current: 2 }, 2000)
  expect(phaseRemaining(second, { ...job, progress_current: 2 }, 2000)).toBeNull()
})
it('estimates only the measured current phase and decays between updates', () => {
  let pace = notePhasePace(undefined, job, 0)
  pace = notePhasePace(pace, { ...job, progress_current: 2 }, 2000)
  pace = notePhasePace(pace, { ...job, progress_current: 3 }, 4000)
  expect(phaseRemaining(pace, { ...job, progress_current: 3 }, 5000)).toBe(13)
  expect(phaseRemaining(pace, { ...job, phase: 'encode' }, 5000)).toBeNull()
  expect(phaseRemaining(pace, { ...job, shot_index: 2 }, 5000)).toBeNull()
})
it('ignores duplicate observations and resets for a different job', () => {
  const first = notePhasePace(undefined, job, 0)
  expect(notePhasePace(first, job, 5000)).toEqual(first)
  expect(notePhasePace(first, { ...job, id: 'b'.repeat(32) }, 5000).deltas).toBe(0)
})
