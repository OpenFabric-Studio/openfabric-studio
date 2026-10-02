import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as audioPlayback from '../composables/audioPlayback'
import { useTimelineEngine } from '../composables/useTimelineEngine'
import { defaultChannelSettings, defaultMasterSettings } from './mixerEngine'
import type { TimelineProject } from './timelineTypes'
import { TestAudioBuffer, TestAudioContext } from './testWebAudio'

beforeEach(() => {
  TestAudioContext.instances = []
  vi.stubGlobal('AudioContext', TestAudioContext)
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  vi.spyOn(audioPlayback, 'getSharedAudioCtx').mockReturnValue(new AudioContext())
})

afterEach(() => vi.unstubAllGlobals())

function project(laneCount = 1): TimelineProject {
  return {
    version: 1, bpm: 120, pxPerSecond: 100, snapEnabled: true,
    master: defaultMasterSettings(),
    lanes: Array.from({ length: laneCount }, (_, index) => ({
      id: String(index), name: `Lane ${index}`, settings: defaultChannelSettings(),
      clips: [{ id: `clip-${index}`, sourceUrl: '/audio.wav', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 4 }],
    })),
  }
}

function buffers(): Map<string, AudioBuffer> {
  return new Map([['/audio.wav', new AudioBuffer({ numberOfChannels: 1, length: 32000, sampleRate: 8000 })]])
}

it('keeps source routing, retained lane identity and the master when lanes are added or reordered', async () => {
  const engine = useTimelineEngine()
  const originalGraph = engine.ensureGraph(['0'])
  const retained = originalGraph.lanes[0]
  const master = originalGraph.master
  await engine.play(project(), buffers(), 0)
  const context = TestAudioContext.instances[0]
  const oldSource = context.nodes.find((node) => node.kind === 'buffer-source')

  const changed = engine.ensureGraph(['new', '0'])

  expect(changed).toBe(originalGraph)
  expect(changed.lanes[1]).toBe(retained)
  expect(changed.master).toBe(master)
  expect(oldSource?.stopCalls).toHaveLength(0)
  expect(oldSource?.connections.size).toBe(1)
  expect(retained?.input).toBeDefined()
  engine.teardown()
})

it('disconnects only a removed lane, including its chorus oscillator and feedback graph', () => {
  const engine = useTimelineEngine()
  const graph = engine.ensureGraph(['0', '1'])
  const removed = graph.lanes[0]
  const retained = graph.lanes[1]
  if (!removed || !retained) throw new Error('Missing test channels')
  const context = TestAudioContext.instances[0]
  const disconnects = removed.nodes.map((node) => vi.spyOn(node, 'disconnect'))
  const removedLfoStop = vi.spyOn(removed.chorusLfo, 'stop')
  const retainedLfoStop = vi.spyOn(retained.chorusLfo, 'stop')
  engine.ensureGraph(['1'])
  expect(graph.lanes[0]).toBe(retained)
  expect(disconnects.every((disconnect) => disconnect.mock.calls.length === 1)).toBe(true)
  expect(removedLfoStop).toHaveBeenCalledTimes(1)
  expect(retainedLfoStop).not.toHaveBeenCalled()
  engine.teardown()
  expect(context.nodes.every((node) => node.connections.size === 0)).toBe(true)
})

it('anchors playback to the context time after audio resume actually completes', async () => {
  const engine = useTimelineEngine()
  const context = TestAudioContext.instances[0]
  let resolveResume = (): void => {}
  context.resumeResult = new Promise<void>((resolve) => { resolveResume = resolve })
  const pending = engine.play(project(), buffers(), 1)
  context.currentTime = 12
  resolveResume()
  expect(await pending).toBe(12.05)
  expect(context.nodes.find((node) => node.kind === 'buffer-source')?.startCalls).toEqual([[12.05, 1, 3]])
  engine.teardown()
})

it.each(['stop', 'teardown'] as const)('invalidates an awaited resume after %s', async (action) => {
  const engine = useTimelineEngine()
  const context = TestAudioContext.instances[0]
  let resolveResume = (): void => {}
  context.resumeResult = new Promise<void>((resolve) => { resolveResume = resolve })
  const pending = engine.play(project(), buffers(), 0)

  engine[action]()
  resolveResume()
  expect(await pending).toBeNull()

  expect(context.nodes.filter((node) => node.kind === 'buffer-source')).toHaveLength(0)
  engine.teardown()
})

it('does not route a stale resumed project into replacement lane channels', async () => {
  const engine = useTimelineEngine()
  const context = TestAudioContext.instances[0]
  let resolveResume = (): void => {}
  context.resumeResult = new Promise<void>((resolve) => { resolveResume = resolve })
  const pending = engine.play(project(), buffers(), 0)
  engine.ensureGraph(['replacement'])
  resolveResume()
  expect(await pending).toBeNull()
  expect(context.nodes.filter((node) => node.kind === 'buffer-source')).toHaveLength(0)
  engine.teardown()
})

it('uses updated lane identities when the same project adds a lane while resume is pending', async () => {
  const engine = useTimelineEngine()
  const context = TestAudioContext.instances[0]
  let resolveResume = (): void => {}
  context.resumeResult = new Promise<void>((resolve) => { resolveResume = resolve })
  const current = project()
  const pending = engine.play(current, buffers(), 0)
  const newLane = project(2).lanes[1]
  if (!newLane) throw new Error('Missing added lane')
  current.lanes.push(newLane)
  engine.ensureGraph(current.lanes.map((lane) => lane.id))
  resolveResume()
  expect(await pending).toBe(5.05)
  expect(context.nodes.filter((node) => node.kind === 'buffer-source')).toHaveLength(2)
  engine.teardown()
})

it('only schedules the newest seek when multiple resumes are awaiting audio activation', async () => {
  const engine = useTimelineEngine()
  const context = TestAudioContext.instances[0]
  let resolveResume = (): void => {}
  context.resumeResult = new Promise<void>((resolve) => { resolveResume = resolve })
  const old = engine.play(project(), buffers(), 0)
  const latest = engine.play(project(), buffers(), 2)
  resolveResume()
  expect(await old).toBeNull()
  expect(await latest).toBe(5.05)
  expect(context.nodes.filter((node) => node.kind === 'buffer-source').map((node) => node.startCalls)).toEqual([[[5.05, 2, 2]]])
  engine.teardown()
})

it('pre-schedules the next pass at the exact boundary and stops every pass on seek or stop', async () => {
  const engine = useTimelineEngine()
  const clipBuffers = buffers()
  await engine.play(project(), clipBuffers, 1, 3)
  engine.queuePass(project(), clipBuffers, 1, 3, 7.05)
  const context = TestAudioContext.instances[0]
  const sources = context.nodes.filter((node) => node.kind === 'buffer-source')
  expect(sources.map((node) => node.startCalls)).toEqual([[[5.05, 1, 2]], [[7.05, 1, 2]]])
  await engine.play(project(2), clipBuffers, 1.5)
  expect(sources[0]?.connections.size).toBe(0)
  expect(sources.every((node) => node.stopCalls.length === 1)).toBe(true)
  const replacements = context.nodes.filter((node) => node.kind === 'buffer-source').slice(2)
  expect(replacements.map((node) => node.startCalls[0]?.slice(1))).toEqual([[1.5, 2.5], [1.5, 2.5]])
  engine.stop()
  engine.queuePass(project(), clipBuffers, 1, 3, 9.05)
  expect(context.nodes.filter((node) => node.kind === 'buffer-source')).toHaveLength(4)
  expect(replacements.every((node) => node.connections.size === 0 && node.stopCalls.length === 1)).toBe(true)
  engine.teardown()
})
