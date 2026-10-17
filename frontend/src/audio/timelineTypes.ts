import type { ChannelSettings, MasterSettings } from './mixerEngine'
import type { MidiNote } from './miniMidiPlayer'
import type { BeatClipLink, MusicProjectArtifact, MusicProjectProvenance } from '../api/generated'

export type ClipType = 'audio' | 'midi'

export interface Clip {
  id: string
  type?: ClipType
  sourceUrl?: string
  sourceLabel: string
  /** Position on the global timeline, in seconds. */
  timelineStart: number
  /** Offset into the source audio where playback of this clip begins, in seconds. */
  trimStart: number
  /** Offset into the source audio where playback of this clip ends, in seconds. */
  trimEnd: number
  fadeInDuration?: number
  fadeOutDuration?: number
  warpEnabled?: boolean
  originalBpm?: number
  notes?: MidiNote[]
  instrument?: 'sawtooth' | 'square' | 'sine' | 'triangle'
  muted?: boolean
  solo?: boolean
  beatLink?: BeatClipLink
  sourceArtifact?: MusicProjectArtifact
}

export interface TimelineLane {
  id: string
  name: string
  clips: Clip[]
  settings: ChannelSettings
  colorId?: string
}

export interface TimelineProject {
  version: 1
  lanes: TimelineLane[]
  master: MasterSettings
  pxPerSecond: number
  bpm: number
  snapEnabled: boolean
  loopRegion?: { start: number; end: number; enabled: boolean }
  musicProvenance?: MusicProjectProvenance
}

/**
 * Ratio between stretched (timeline) seconds and source seconds for a clip.
 * A warped clip's stretched buffer has length `source / (projectBpm / originalBpm)`,
 * so every source-domain value (trimStart, trimEnd, note times) is multiplied by
 * `originalBpm / projectBpm` to land in the stretched domain. This is the single
 * place that formula lives; the drawing code, snapping, split/duplicate and the
 * playback/render engine must all go through it so they cannot disagree.
 */
export function stretchFactor(clip: Pick<Clip, 'warpEnabled' | 'originalBpm'>, projectBpm: number = 120): number {
  if (clip.warpEnabled && clip.originalBpm) {
    return clip.originalBpm / (projectBpm || 120)
  }
  return 1.0
}

/** Buffer offsets must use the time domain of the buffer actually available. */
export function clipBuffer(clip: Clip, projectBpm: number, buffers: Map<string, AudioBuffer>): { buffer: AudioBuffer | null; stretchFactor: number } {
  if (!clip.sourceUrl) return { buffer: null, stretchFactor: 1 }
  if (clip.warpEnabled && clip.originalBpm) {
    const warped = buffers.get(`${clip.sourceUrl}_warp_${clip.originalBpm}_${projectBpm || 120}`)
    if (warped) return { buffer: warped, stretchFactor: stretchFactor(clip, projectBpm) }
  }
  return { buffer: buffers.get(clip.sourceUrl) ?? null, stretchFactor: 1 }
}

export function clipDuration(clip: Clip, projectBpm: number = 120): number {
  return (clip.trimEnd - clip.trimStart) * stretchFactor(clip, projectBpm)
}

export function clipEnd(clip: Clip, projectBpm: number = 120): number {
  return clip.timelineStart + clipDuration(clip, projectBpm)
}

export function projectDuration(project: TimelineProject): number {
  let max = 0
  for (const lane of project.lanes) {
    for (const clip of lane.clips) {
      max = Math.max(max, clipEnd(clip, project.bpm))
    }
  }
  return max
}
