/** Same walk as cover_seconds / shot_ends in backend/app/video_jobs.py. */

const MAX_SHOTS = 40

export function coverSeconds(remaining: number): number | null {
  if (remaining < 2 - 0.05) return null
  if (remaining >= 8 + 2 - 0.05) return 8
  for (const length of [12, 10, 8, 6, 4, 2]) {
    if (length <= remaining + 0.25) return length
  }
  return null
}

export function shotEnds(durationSec: number): number[] {
  if (!Number.isFinite(durationSec) || durationSec <= 0) return []
  const ends: number[] = []
  let cursor = 0
  while (ends.length < MAX_SHOTS) {
    const seconds = coverSeconds(durationSec - cursor)
    if (seconds == null) break
    cursor += seconds
    ends.push(cursor)
  }
  return ends
}
