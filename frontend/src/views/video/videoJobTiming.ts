import type { VideoProjectJob } from '../../api/contracts'
export interface VideoPhasePace { identity: string; current: number; total: number; at: number; deltas: number; secondsPerUnit: number | null }
function identity(job: VideoProjectJob): string { return `${job.id}:${job.phase ?? ''}:${job.shot_index ?? 0}:${job.progress_total ?? 0}` }
export function notePhasePace(previous: VideoPhasePace | undefined, job: VideoProjectJob, now: number): VideoPhasePace {
  const key = identity(job), current = job.progress_current ?? 0, total = job.progress_total ?? 0
  if (!previous || previous.identity !== key || current < previous.current) return { identity: key, current, total, at: now, deltas: 0, secondsPerUnit: null }
  if (current === previous.current || now <= previous.at) return previous
  const measured = (now - previous.at) / 1000 / (current - previous.current)
  return { identity: key, current, total, at: now, deltas: previous.deltas + 1,
    secondsPerUnit: previous.secondsPerUnit === null ? measured : previous.secondsPerUnit * .6 + measured * .4 }
}
export function phaseRemaining(pace: VideoPhasePace | undefined, job: VideoProjectJob, now: number): number | null {
  if (!pace || pace.identity !== identity(job) || job.status !== 'running' || pace.deltas < 2 || pace.secondsPerUnit === null || pace.total <= 0) return null
  return Math.max(0, Math.round((pace.total - pace.current) * pace.secondsPerUnit - Math.max(0, now - pace.at) / 1000))
}
