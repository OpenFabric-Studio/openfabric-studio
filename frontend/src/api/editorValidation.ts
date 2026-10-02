import { isObject } from './schemaValidation'
import { defaultMasterSettings } from '../audio/mixerEngine'
import type { ChannelSettings, MasterSettings, MixSettings } from '../audio/mixerEngine'
import type { Clip, TimelineLane, TimelineProject } from '../audio/timelineTypes'
import type { MidiNote } from '../audio/miniMidiPlayer'

function invalid(): never {
  throw new TypeError('Invalid saved editor settings')
}

function object(value: unknown): Record<string, unknown> {
  return isObject(value) ? value : invalid()
}

function string(value: unknown): string {
  return typeof value === 'string' ? value : invalid()
}

function number(value: unknown, min = -Infinity, max = Infinity): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max ? value : invalid()
}

function integer(value: unknown, min: number, max: number): number {
  const result = number(value, min, max)
  return Number.isInteger(result) ? result : invalid()
}

function boolean(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalid()
}

function optional<T>(value: unknown, parse: (value: unknown) => T): T | undefined {
  return value === undefined ? undefined : parse(value)
}

function array<T>(value: unknown, parse: (value: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(parse) : invalid()
}

function literal<const T extends string>(value: unknown, choices: readonly T[]): T {
  for (const choice of choices) if (value === choice) return choice
  return invalid()
}

function effect(value: unknown, fallback: object): Record<string, unknown> {
  return object(value === undefined ? fallback : value)
}

function parseMasterSettings(value: unknown): MasterSettings {
  const row = object(value)
  const eq = object(row.eq), comp = object(row.comp), reverb = object(row.reverb)
  // Earlier editor documents predate the optional effect rack. Preserve the
  // existing engine's defaults for absent effects; malformed effects still fail.
  const defaults = defaultMasterSettings()
  const filter = effect(row.filter, defaults.filter)
  const distortion = effect(row.distortion, defaults.distortion)
  const chorus = effect(row.chorus, defaults.chorus)
  const bitcrusher = effect(row.bitcrusher, defaults.bitcrusher)
  const delay = effect(row.delay, defaults.delay)
  return {
    volume: number(row.volume, 0, 1.5),
    eq: { low: number(eq.low, -12, 12), mid: number(eq.mid, -12, 12), high: number(eq.high, -12, 12) },
    comp: { threshold: number(comp.threshold, -60, 0), ratio: number(comp.ratio, 1, 20) },
    reverb: { mix: number(reverb.mix, 0, 1) },
    filter: {
      enabled: boolean(filter.enabled), type: literal(filter.type, ['lowpass', 'highpass']),
      frequency: number(filter.frequency, 20, 22000), resonance: number(filter.resonance, 0.1, 20),
    },
    distortion: { enabled: boolean(distortion.enabled), amount: number(distortion.amount, 0, 1), mix: number(distortion.mix, 0, 1) },
    chorus: { enabled: boolean(chorus.enabled), rate: number(chorus.rate, 0.1, 5), depth: number(chorus.depth, 0.001, 0.01), mix: number(chorus.mix, 0, 1) },
    bitcrusher: { enabled: boolean(bitcrusher.enabled), bits: integer(bitcrusher.bits, 2, 16), mix: number(bitcrusher.mix, 0, 1) },
    delay: { enabled: boolean(delay.enabled), time: number(delay.time, 0.01, 2), feedback: number(delay.feedback, 0, 0.95), mix: number(delay.mix, 0, 1) },
  }
}

function parseChannelSettings(value: unknown): ChannelSettings {
  const row = object(value)
  return { ...parseMasterSettings(value), pan: number(row.pan, -1, 1), muted: boolean(row.muted), solo: boolean(row.solo) }
}

export function parseMixSettings(value: unknown): MixSettings {
  const row = object(value), stems = object(row.stems)
  if (row.version !== 1) return invalid()
  return {
    version: 1,
    master: parseMasterSettings(row.master),
    stems: {
      vocals: parseChannelSettings(stems.vocals), drums: parseChannelSettings(stems.drums),
      bass: parseChannelSettings(stems.bass), other: parseChannelSettings(stems.other),
    },
  }
}

function parseNote(value: unknown): MidiNote {
  const row = object(value)
  return {
    note: integer(row.note, 0, 127), channel: integer(row.channel, 0, 15), velocity: number(row.velocity, 0, 1),
    startSec: number(row.startSec, 0), durationSec: number(row.durationSec, 0),
  }
}

function parseClip(value: unknown): Clip {
  const row = object(value)
  const trimStart = number(row.trimStart, 0), trimEnd = number(row.trimEnd, trimStart)
  return {
    id: string(row.id), type: optional(row.type, (item) => literal(item, ['audio', 'midi'])),
    sourceUrl: optional(row.sourceUrl, string), sourceLabel: string(row.sourceLabel),
    timelineStart: number(row.timelineStart, 0), trimStart, trimEnd,
    fadeInDuration: optional(row.fadeInDuration, (item) => number(item, 0)),
    fadeOutDuration: optional(row.fadeOutDuration, (item) => number(item, 0)),
    warpEnabled: optional(row.warpEnabled, boolean),
    originalBpm: optional(row.originalBpm, (item) => number(item, 20, 999)),
    notes: optional(row.notes, (item) => array(item, parseNote)),
    instrument: optional(row.instrument, (item) => literal(item, ['sawtooth', 'square', 'sine', 'triangle'])),
    muted: optional(row.muted, boolean), solo: optional(row.solo, boolean),
  }
}

function parseLane(value: unknown): TimelineLane {
  const row = object(value)
  return {
    id: string(row.id), name: string(row.name), clips: array(row.clips, parseClip),
    settings: parseChannelSettings(row.settings), colorId: optional(row.colorId, string),
  }
}

export function parseTimelineProject(value: unknown): TimelineProject {
  const row = object(value)
  if (row.version !== 1) return invalid()
  const loopRegion = optional(row.loopRegion, (value) => {
    const loop = object(value), start = number(loop.start, 0)
    return { start, end: number(loop.end, start), enabled: boolean(loop.enabled) }
  })
  return {
    version: 1, lanes: array(row.lanes, parseLane), master: parseMasterSettings(row.master),
    pxPerSecond: number(row.pxPerSecond, 5, 400), bpm: number(row.bpm, 20, 999),
    snapEnabled: boolean(row.snapEnabled), loopRegion,
  }
}
