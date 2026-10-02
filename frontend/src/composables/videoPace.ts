/** Pace of one LTX denoise step, used to estimate the rest of the video. */

export const FALLBACK_STEP_SEC = 36

export interface PaceSample {
  phase: string
  shot: number
  step: number
  total: number
  shotCount: number
}

export interface VideoPace {
  at: number
  shot: number
  step: number
  total: number
  stepSec: number | null
}

export function noteVideoPace(prev: VideoPace | undefined, sample: PaceSample, now: number): VideoPace {
  if (!prev) {
    return { at: now, shot: sample.shot, step: sample.step, total: sample.total, stepSec: null }
  }
  const sameBar = sample.shot === prev.shot && sample.total > 0 && sample.total === prev.total
  if (sameBar && sample.step > prev.step) {
    const per = ((now - prev.at) / 1000) / (sample.step - prev.step)
    const stepSec = prev.stepSec == null ? per : prev.stepSec * 0.6 + per * 0.4
    return { at: now, shot: sample.shot, step: sample.step, total: sample.total, stepSec }
  }
  if (sample.shot !== prev.shot || (sample.total > 0 && sample.total !== prev.total)) {
    return {
      at: now,
      shot: sample.shot,
      step: sample.step,
      total: sample.total || prev.total,
      stepSec: prev.stepSec,
    }
  }
  return prev
}

export function videoRemainingSec(pace: VideoPace | undefined, sample: PaceSample, now: number): number | null {
  if (sample.phase === 'download' || sample.phase === 'mux' || sample.phase === '') return null
  const denoise = sample.phase === 'denoise' && sample.total > 0
  const laterShot = sample.phase === 'starting' && pace?.stepSec != null && sample.shot > 1
  if (!denoise && !laterShot) return null
  const per = pace?.stepSec != null && pace.stepSec > 0 ? pace.stepSec : FALLBACK_STEP_SEC
  const total = denoise ? sample.total : (pace?.total || sample.total || 30)
  const shot = Math.max(1, sample.shot || 1)
  const shotCount = Math.max(shot, sample.shotCount || shot)
  const later = shotCount - shot
  const stepsLeft = (denoise ? Math.max(0, total - sample.step) : total) + later * total
  const since = pace ? Math.max(0, (now - pace.at) / 1000) : 0
  return Math.max(0, Math.round(stepsLeft * per - since))
}

export function videoHasLaterShots(sample: PaceSample): boolean {
  const shot = Math.max(1, sample.shot || 1)
  return (sample.shotCount || 0) > shot
}
