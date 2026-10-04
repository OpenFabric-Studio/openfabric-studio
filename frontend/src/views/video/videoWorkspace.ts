import type { VideoProjectShot, VideoShotDraft } from '../../api/contracts'

export const videoWorkspaceSteps = ['song', 'direction', 'storyboard', 'preview', 'export'] as const
export type VideoWorkspaceStep = typeof videoWorkspaceSteps[number]
export const videoClipLengths = [2, 4, 6, 8, 10, 12] as const
export type VideoClipLength = typeof videoClipLengths[number]
export type ShotProblem = 'bad_prompt' | 'bad_start' | 'bad_length' | 'past_end' | 'overlap' | 'frame_alignment' | ''

export function newVideoId(): string {
  return crypto.randomUUID().replaceAll('-', '')
}

export function isClipLength(value: number): value is VideoClipLength {
  return videoClipLengths.some((length) => value === length)
}

export function frameTime(seconds: number): number {
  return Math.round(seconds * 24) / 24
}

export function toShotDraft(shot: VideoProjectShot): VideoShotDraft {
  return { id: shot.id, start_sec: shot.start_sec, seconds: shot.seconds ?? 4, prompt: shot.prompt,
    seed: shot.seed ?? 0, reference_id: shot.reference_id ?? null, reference_strength: shot.reference_strength ?? .7,
    locked: shot.locked ?? false }
}

export function shotProblem(shots: readonly VideoShotDraft[], id: string, duration: number): ShotProblem {
  const shot = shots.find((item) => item.id === id)
  if (!shot) return ''
  if (!shot.prompt.trim() || shot.prompt.length > 2000) return 'bad_prompt'
  if (!Number.isFinite(shot.start_sec) || shot.start_sec < 0) return 'bad_start'
  if (Math.abs(shot.start_sec * 24 - Math.round(shot.start_sec * 24)) > 1e-5) return 'frame_alignment'
  const length = shot.seconds ?? 4
  if (!isClipLength(length)) return 'bad_length'
  if (shot.start_sec >= duration || shot.start_sec + length > duration + 1 / 24) return 'past_end'
  if (shots.some((other) => other.id !== id && other.start_sec < shot.start_sec + length - 1e-6
    && other.start_sec + (other.seconds ?? 4) > shot.start_sec + 1e-6)) return 'overlap'
  return ''
}

export function changeShotLength(shots: readonly VideoShotDraft[], id: string, length: VideoClipLength, ripple: boolean): VideoShotDraft[] {
  const index = shots.findIndex((shot) => shot.id === id)
  const selected = shots[index]
  if (!selected) return [...shots]
  const delta = length - (selected.seconds ?? 4)
  return shots.map((shot, position) => position === index ? { ...shot, seconds: length }
    : ripple && position > index ? { ...shot, start_sec: frameTime(shot.start_sec + delta) } : { ...shot })
}

export function splitShot(shots: readonly VideoShotDraft[], id: string, newId: string): VideoShotDraft[] {
  const index = shots.findIndex((shot) => shot.id === id)
  const selected = shots[index]
  const length = selected?.seconds ?? 4
  if (!selected || length === 2) return [...shots]
  const splits: Record<Exclude<VideoClipLength, 2>, readonly [VideoClipLength, VideoClipLength]> = {
    4: [2, 2], 6: [2, 4], 8: [4, 4], 10: [4, 6], 12: [6, 6],
  }
  const [left, right] = splits[length]
  return [...shots.slice(0, index), { ...selected, seconds: left }, { ...selected, id: newId,
    start_sec: frameTime(selected.start_sec + left), seconds: right, seed: ((selected.seed ?? 0) + 1) % 2147483648 }, ...shots.slice(index + 1)]
}

export function duplicateShot(shots: readonly VideoShotDraft[], id: string, newId: string): VideoShotDraft[] {
  const index = shots.findIndex((shot) => shot.id === id)
  const selected = shots[index]
  if (!selected) return [...shots]
  const seconds = selected.seconds ?? 4
  return [...shots.slice(0, index + 1), { ...selected, id: newId, start_sec: frameTime(selected.start_sec + seconds),
    seed: ((selected.seed ?? 0) + 1) % 2147483648 }, ...shots.slice(index + 1).map((shot) => ({ ...shot, start_sec: frameTime(shot.start_sec + seconds) }))]
}

export function moveShot(shots: readonly VideoShotDraft[], id: string, direction: -1 | 1): VideoShotDraft[] {
  const index = shots.findIndex((shot) => shot.id === id)
  const target = index + direction
  if (index < 0 || target < 0 || target >= shots.length) return [...shots]
  const reordered = [...shots]
  const [selected] = reordered.splice(index, 1)
  if (!selected) return [...shots]
  reordered.splice(target, 0, selected)
  let cursor = shots[0]?.start_sec ?? 0
  return reordered.map((shot) => {
    const result = { ...shot, start_sec: frameTime(cursor) }
    cursor += shot.seconds ?? 4
    return result
  })
}

export function resolvedStillId(
  project: { track_id?: number | null; mode?: string | null; character_lock?: boolean; references?: readonly { id: string }[] | null },
  shot: { reference_id?: string | null },
): string | null {
  if (shot.reference_id) return shot.reference_id
  const references = project.references ?? []
  if (project.mode && project.mode !== 'generated' && references[0]) return references[0].id
  if (project.track_id == null && references.length === 1 && references[0]) return references[0].id
  return null
}

export function characterLockIssue(
  project: { track_id?: number | null; mode?: string | null; character_lock?: boolean; references?: readonly { id: string }[] | null },
  shots: readonly { reference_id?: string | null }[],
): '' | 'missing' | 'mismatch' {
  if (!project.character_lock || project.track_id != null || !shots.length) return ''
  const ids = shots.map((shot) => resolvedStillId(project, shot))
  if (ids.some((id) => !id)) return 'missing'
  if (new Set(ids).size > 1) return 'mismatch'
  return ''
}
