import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  buildTimelineGraph, disconnectTimelineGraph, renderTimeline, scheduleTimeline,
} from './timelineEngine'
import { buildMixGraph, defaultChannelSettings, defaultMasterSettings, defaultMixSettings, disconnectMixGraph, renderMix } from './mixerEngine'
import type { ScheduledClip } from './timelineEngine'
import { TestAudioBuffer, TestAudioContext, TestAudioNode, TestOfflineAudioContext } from './testWebAudio'

beforeEach(() => {
  TestAudioContext.instances = []
  TestOfflineAudioContext.renderError = null
  vi.stubGlobal('AudioContext', TestAudioContext)
  vi.stubGlobal('OfflineAudioContext', TestOfflineAudioContext)
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
})

afterEach(() => vi.unstubAllGlobals())

function impulse(): AudioBuffer {
  return new AudioBuffer({ numberOfChannels: 2, length: 8, sampleRate: 8000 })
}

function midiClip(instrument?: OscillatorType): ScheduledClip {
  return {
    laneIndex: 0,
    type: 'midi',
    timelineStart: 2,
    trimStart: 0.1,
    trimEnd: 0.9,
    stretchFactor: 1.5,
    instrument,
    notes: [{ note: 69, startSec: 0.05, durationSec: 0.8, velocity: 0.8, channel: 0 }],
  }
}

describe('timeline preview and export', () => {
  it('cuts stretched source trims and their fade envelope at the loop boundary', () => {
    const graph = buildTimelineGraph(new AudioContext(), 1, impulse())
    const context = TestAudioContext.instances[0]
    const before = context.nodes.length
    const clip: ScheduledClip = {
      laneIndex: 0, buffer: new AudioBuffer({ numberOfChannels: 1, length: 80000, sampleRate: 8000 }),
      timelineStart: 2, trimStart: 1, trimEnd: 4, stretchFactor: 2,
    }
    scheduleTimeline(graph, [clip], 3, 10, () => {}, 5)
    const nodes = context.nodes.slice(before)
    expect(nodes.find((node) => node.kind === 'buffer-source')?.startCalls).toEqual([[10, 3, 2]])
    const envelope = nodes.find((node) => node.kind === 'gain')?.gain.events
    expect(envelope?.every((event) => event.time <= 12)).toBe(true)
    expect(envelope?.at(-1)).toEqual({ kind: 'ramp', value: 0, time: 12 })
  })

  it('cuts a stretched MIDI note at the loop end and excludes notes starting there', () => {
    const graph = buildTimelineGraph(new AudioContext(), 1, impulse())
    const context = TestAudioContext.instances[0]
    const before = context.nodes.length
    const clip = midiClip('square')
    clip.notes?.push({ note: 72, startSec: 0.5, durationSec: 1, velocity: 1, channel: 0 })
    scheduleTimeline(graph, [clip], 2.1, 10, () => {}, 2.6)
    const oscillators = context.nodes.slice(before).filter((node) => node.kind === 'oscillator')
    expect(oscillators).toHaveLength(1)
    expect(oscillators[0]?.startCalls).toEqual([[10]])
    expect(oscillators[0]?.stopCalls[0]).toBeCloseTo(10.5)
  })

  it('does not substitute the final sample when a trim starts beyond the source buffer', () => {
    const graph = buildTimelineGraph(new AudioContext(), 1, impulse())
    scheduleTimeline(graph, [{ laneIndex: 0, buffer: impulse(), timelineStart: 0, trimStart: 1, trimEnd: 2 }], 0, 0, () => {})
    expect(TestAudioContext.instances[0].nodes.filter((node) => node.kind === 'buffer-source')).toHaveLength(0)
  })

  it.each<OscillatorType>(['sawtooth', 'square', 'sine', 'triangle'])('uses the selected %s waveform and the same note envelope in export', async (instrument) => {
    const graph = buildTimelineGraph(new AudioContext(), 1, impulse())
    const preview = TestAudioContext.instances[0]
    const previewNodeCount = preview.nodes.length
    scheduleTimeline(graph, [midiClip(instrument)], 0, 0, () => {})
    const previewNodes = preview.nodes.slice(previewNodeCount)

    await renderTimeline([midiClip(instrument)], [defaultChannelSettings()], defaultMasterSettings(), 4, 8000)
    const exported = TestAudioContext.instances[1]
    const renderedOscillator = exported.nodes.filter((node) => node.kind === 'oscillator').at(-1)
    const previewOscillator = previewNodes.find((node) => node.kind === 'oscillator')
    const previewEnvelope = previewNodes.find((node) => node.kind === 'gain')
    const renderedEnvelope = exported.nodes.at(-1)

    expect(renderedOscillator?.type).toBe(instrument)
    expect(renderedOscillator?.frequency.value).toBe(previewOscillator?.frequency.value)
    expect(renderedOscillator?.startCalls).toEqual(previewOscillator?.startCalls)
    expect(renderedOscillator?.stopCalls[0]).toEqual(previewOscillator?.stopCalls[0])
    expect(renderedEnvelope?.gain.events).toEqual(previewEnvelope?.gain.events)
  })

  it('stops all chorus oscillators and disconnects every timeline node including destinations and feedback loops', () => {
    const graph = buildTimelineGraph(new AudioContext(), 2, impulse())
    const context = TestAudioContext.instances[0]
    disconnectTimelineGraph(graph)

    expect(context.nodes.filter((node) => node.kind === 'oscillator').every((node) => node.stopCalls.length === 1)).toBe(true)
    expect(context.nodes.filter((node) => node.kind !== 'destination').every((node) => node.disconnectCalls > 0)).toBe(true)
    expect(context.nodes.every((node) => node.connections.size === 0)).toBe(true)
  })

  it('disconnects every mixer node as well', () => {
    const graph = buildMixGraph(new AudioContext(), impulse())
    disconnectMixGraph(graph)
    expect(TestAudioContext.instances[0].nodes.every((node) => node.connections.size === 0)).toBe(true)
  })

  it('disposes offline nodes even when rendering fails', async () => {
    TestOfflineAudioContext.renderError = new Error('Rendering failed')
    await expect(renderTimeline([midiClip('square')], [defaultChannelSettings()], defaultMasterSettings(), 4, 8000)).rejects.toThrow('Rendering failed')
    expect(TestAudioContext.instances[0].nodes.filter((node) => node.kind !== 'destination').every((node) => node.disconnectCalls > 0)).toBe(true)
  })

  it('disposes partially scheduled timeline sources if a source cannot start', async () => {
    vi.spyOn(TestAudioNode.prototype, 'start').mockImplementation(function (this: TestAudioNode, ...args: number[]) {
      if (this.kind === 'buffer-source') throw new Error('Source cannot start')
      this.startCalls.push(args)
    })
    const audioClip: ScheduledClip = { laneIndex: 0, buffer: impulse(), timelineStart: 0, trimStart: 0, trimEnd: 0.001 }

    await expect(renderTimeline([midiClip('square'), audioClip], [defaultChannelSettings()], defaultMasterSettings(), 4, 8000)).rejects.toThrow('Source cannot start')

    expect(TestAudioContext.instances[0].nodes.filter((node) => node.kind !== 'destination').every((node) => node.disconnectCalls > 0)).toBe(true)
  })

  it.each([false, true])('disposes mixer offline graphs after rendering (failure=%s)', async (fail) => {
    if (fail) TestOfflineAudioContext.renderError = new Error('Rendering failed')
    const buffer = impulse()
    const rendered = renderMix({ vocals: buffer, drums: buffer, bass: buffer, other: buffer }, defaultMixSettings(), 8000)
    if (fail) await expect(rendered).rejects.toThrow('Rendering failed')
    else await rendered

    expect(TestAudioContext.instances[0].nodes.filter((node) => node.kind !== 'destination').every((node) => node.disconnectCalls > 0)).toBe(true)
  })
})
