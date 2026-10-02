/**
 * Thin orchestration layer around audio/timelineEngine.ts. Deliberately a
 * plain factory function (not a module-level singleton, not Pinia state) -
 * each call creates its own closures over the live Web Audio graph/playback
 * handle, so this must be instantiated once per EditorPage mount and torn
 * down in onBeforeUnmount, exactly mirroring MixerModal.vue's local
 * `let graph`/`let playback` pattern. Keeping this out of the Pinia store
 * (stores/editor.ts) is deliberate: a store update via HMR would otherwise
 * silently orphan connected AudioNodes with no disconnect() ever firing.
 */
import { getSharedAudioCtx } from './audioPlayback'
import { buildChannel, decodeStem, disconnectChannel, getChannelLevel, getReverbImpulse } from '../audio/mixerEngine'
import type { BuiltChannel } from '../audio/mixerEngine'
import {
  applyLaneSettings,
  applyMasterSettings,
  buildTimelineGraph,
  disconnectTimelineGraph,
  effectiveLaneGain,
  renderTimeline,
  scheduleTimeline,
} from '../audio/timelineEngine'
import type { ScheduledClip, TimelineGraph, TimelinePlaybackHandle } from '../audio/timelineEngine'
import { stretchFactor as clipStretchFactor } from '../audio/timelineTypes'
import type { TimelineProject } from '../audio/timelineTypes'

export function useTimelineEngine() {
  let graph: TimelineGraph | null = null
  const laneChannels = new Map<string, BuiltChannel>()
  let passes: TimelinePlaybackHandle[] = []
  let seekToken = 0

  function ensureGraph(laneIds: readonly string[]): TimelineGraph {
    const ctx = getSharedAudioCtx()
    if (!graph) graph = buildTimelineGraph(ctx, 0, getReverbImpulse(ctx.sampleRate))
    const ids = new Set(laneIds)
    for (const [id, channel] of laneChannels) {
      if (!ids.has(id)) {
        disconnectChannel(channel)
        laneChannels.delete(id)
      }
    }
    const master = graph.master
    graph.lanes = laneIds.map((id) => {
      const existing = laneChannels.get(id)
      if (existing) return existing
      const channel = buildChannel(ctx, false, getReverbImpulse(ctx.sampleRate))
      channel.output.connect(master.input)
      laneChannels.set(id, channel)
      return channel
    })
    return graph
  }

  function applySettings(project: TimelineProject): void {
    const currentGraph = graph
    if (!currentGraph) return
    const anySolo = project.lanes.some((l) => l.settings.solo)
    project.lanes.forEach((lane, i) => applyLaneSettings(currentGraph, i, lane.settings, effectiveLaneGain(lane.settings, anySolo)))
    applyMasterSettings(currentGraph, project.master)
  }

  /**
   * Decodes every source the project references. A source that 404s or does
   * not decode is left out of the map (and logged) instead of failing the whole
   * load, so a project with one deleted track still opens; the clip shows as
   * missing and the rest plays.
   */
  async function decodeAll(project: TimelineProject): Promise<Map<string, AudioBuffer>> {
    const urls = new Set<string>()
    for (const lane of project.lanes) for (const clip of lane.clips) if (clip.sourceUrl) urls.add(clip.sourceUrl)
    const list = [...urls]
    const results = await Promise.allSettled(list.map((u) => decodeStem(u)))
    const buffers = new Map<string, AudioBuffer>()
    results.forEach((r, i) => {
      if (r.status === 'fulfilled') buffers.set(list[i], r.value)
      else console.warn(`editor: could not load ${list[i]}`, r.reason)
    })
    return buffers
  }

  function toScheduledClips(project: TimelineProject, buffers: Map<string, AudioBuffer>): ScheduledClip[] {
    const clips: ScheduledClip[] = []
    const anySolo = project.lanes.some(lane => lane.clips.some(c => c.solo))

    project.lanes.forEach((lane, laneIndex) => {
      const laneClips: ScheduledClip[] = []

      for (const clip of lane.clips) {
        if (clip.muted) continue
        if (anySolo && !clip.solo) continue

        // Same formula the UI draws with (originalBpm / projectBpm). The engine
        // multiplies source-domain trim offsets by this to address the stretched buffer.
        const stretchFactor = clipStretchFactor(clip, project.bpm)
        
        if (clip.type === 'midi') {
          laneClips.push({
            laneIndex,
            type: 'midi',
            notes: clip.notes || [],
            timelineStart: clip.timelineStart,
            trimStart: clip.trimStart,
            trimEnd: clip.trimEnd,
            fadeInDuration: clip.fadeInDuration,
            fadeOutDuration: clip.fadeOutDuration,
            stretchFactor,
            instrument: clip.instrument
          })
          continue
        }

        let buffer = clip.sourceUrl ? buffers.get(clip.sourceUrl) : undefined
        if (clip.warpEnabled && clip.originalBpm && clip.sourceUrl) {
           const bpm = project.bpm || 120
           const key = `${clip.sourceUrl}_warp_${clip.originalBpm}_${bpm}`
           if (buffers.has(key)) {
             buffer = buffers.get(key)
           }
        }
        
        if (buffer) {
          laneClips.push({ 
            laneIndex,
            type: 'audio',
            buffer, 
            timelineStart: clip.timelineStart, 
            trimStart: clip.trimStart, 
            trimEnd: clip.trimEnd,
            fadeInDuration: clip.fadeInDuration,
            fadeOutDuration: clip.fadeOutDuration,
            stretchFactor
          })
        }
      }

      // Auto-crossfade overlapping clips in this lane
      laneClips.sort((a, b) => a.timelineStart - b.timelineStart)
      for (let i = 0; i < laneClips.length - 1; i++) {
        const c1 = laneClips[i]
        const c2 = laneClips[i + 1]
        const c1End = c1.timelineStart + (c1.trimEnd - c1.trimStart) * (c1.stretchFactor || 1.0)
        
        if (c1End > c2.timelineStart) {
          const overlap = c1End - c2.timelineStart
          // Apply crossfade if user hasn't explicitly set a custom fade duration
          if (c1.fadeOutDuration == null) c1.fadeOutDuration = overlap
          if (c2.fadeInDuration == null) c2.fadeInDuration = overlap
        }
      }

      clips.push(...laneClips)
    })
    return clips
  }

  /** Resolves with the actual scheduled start, or null when superseded. */
  async function play(project: TimelineProject, buffers: Map<string, AudioBuffer>, fromSec: number, untilSec = Infinity): Promise<number | null> {
    const g = ensureGraph(project.lanes.map((lane) => lane.id))
    const token = ++seekToken
    await getSharedAudioCtx().resume()
    if (token !== seekToken || graph !== g) return null
    // Retaining the master means graph identity alone no longer detects a
    // different set/order of lanes appearing while audio activation is pending.
    if (project.lanes.length !== g.lanes.length
      || project.lanes.some((lane, index) => laneChannels.get(lane.id) !== g.lanes[index])) return null
    stopPasses()
    applySettings(project)
    const startAt = g.ctx.currentTime + 0.05
    // Project completion belongs to the transport clock, including silent clips.
    passes = [scheduleTimeline(g, toScheduledClips(project, buffers), fromSec, startAt, () => {}, untilSec)]
    return startAt
  }

  /** Queue one loop pass at the prior pass's exact audio boundary. */
  function queuePass(project: TimelineProject, buffers: Map<string, AudioBuffer>, fromSec: number, untilSec: number, ctxStartTime: number): void {
    if (!graph || passes.length === 0) return
    passes.push(scheduleTimeline(graph, toScheduledClips(project, buffers), fromSec, ctxStartTime, () => {}, untilSec))
    while (passes.length > 2) passes.shift()?.stop()
  }

  function stopPasses(): void {
    for (const pass of passes) pass.stop()
    passes = []
  }

  function stop(): void {
    seekToken++ // invalidate any in-flight play()
    stopPasses()
  }

  function teardown(): void {
    stop()
    if (graph) disconnectTimelineGraph(graph)
    graph = null
    laneChannels.clear()
  }

  async function render(project: TimelineProject, buffers: Map<string, AudioBuffer>, totalDurationSec: number): Promise<AudioBuffer> {
    const sampleRate = getSharedAudioCtx().sampleRate
    const clips = toScheduledClips(project, buffers)
    const laneSettings = project.lanes.map((l) => l.settings)
    return renderTimeline(clips, laneSettings, project.master, totalDurationSec, sampleRate)
  }

  function getLaneLevel(laneIndex: number): { peak: number; clipping: boolean; peakL: number; peakR: number } {
    if (!graph || !graph.lanes[laneIndex]) return { peak: 0, clipping: false, peakL: 0, peakR: 0 }
    return getChannelLevel(graph.lanes[laneIndex])
  }

  function getMasterLevel(): { peak: number; clipping: boolean; peakL: number; peakR: number } {
    if (!graph) return { peak: 0, clipping: false, peakL: 0, peakR: 0 }
    return getChannelLevel(graph.master)
  }

  return { ensureGraph, applySettings, decodeAll, toScheduledClips, play, queuePass, stop, teardown, render, getLaneLevel, getMasterLevel }
}
