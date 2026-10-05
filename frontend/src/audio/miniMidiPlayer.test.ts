/// <reference types="node" />
// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { parseMidiBytes, playMidiNotes } from './miniMidiPlayer'
import * as playback from '../composables/audioPlayback'
import { TestAudioContext } from './testWebAudio'

beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('AudioContext', TestAudioContext); TestAudioContext.instances.length = 0 })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

function midi(events: number[], length = events.length): ArrayBuffer {
  return new Uint8Array([0x4d, 0x54, 0x68, 0x64, 0, 0, 0, 6, 0, 0, 0, 1, 0, 96,
    0x4d, 0x54, 0x72, 0x6b, (length >>> 24) & 255, (length >>> 16) & 255, (length >>> 8) & 255, length & 255, ...events]).buffer
}

it('rejects a signed-length backward-offset fixture without hanging the UI', () => {
  // Execute this exact regression with a deadline so a future infinite loop
  // fails the test instead of hanging the test worker.
  const source = readFileSync('src/audio/miniMidiPlayer.ts', 'utf8')
  const parser = source.slice(0, source.indexOf('export interface MidiSynthHandle')).replace(/^import .*$/gm, '')
  const js = ts.transpile(parser.replace(/\bexport /g, ''), { target: ts.ScriptTarget.ES2022 })
  expect(() => runInNewContext(`${js}; parseMidiBytes(input)`, { input: midi([], 0xfffffff0), Uint8Array, DataView, Map, Math }, { timeout: 100 })).not.toThrow()
})

it.each([
  { name: 'truncated chunk', bytes: midi([0, 0x90, 60, 127], 8) },
  { name: 'truncated note', bytes: midi([0, 0x90, 60]) },
  { name: 'variable length over four bytes', bytes: midi([0xff, 0xff, 0xff, 0xff, 0, 0x90, 60, 127]) },
  { name: 'missing running status', bytes: midi([0, 60, 127]) },
  { name: 'oversized meta payload', bytes: midi([0, 0xff, 0x51, 0x7f]) },
  { name: 'zero tempo', bytes: midi([0, 0xff, 0x51, 3, 0, 0, 0, 0, 0x90, 60, 127, 96, 0x80, 60, 0]) },
  { name: 'invalid data byte', bytes: midi([0, 0x90, 60, 0xff]) },
])('rejects malformed MIDI: $name', ({ bytes }) => {
  expect(parseMidiBytes(bytes)).toEqual({ durationSec: 0, notes: [], tempoBpm: 120 })
})

it('preserves a valid tempo, note duration and channel running status', () => {
  const parsed = parseMidiBytes(midi([0, 0xff, 0x51, 3, 7, 0xa1, 0x20, 0, 0x90, 60, 127, 96, 60, 0, 0, 0xff, 0x2f, 0]))
  expect(parsed.tempoBpm).toBe(120)
  expect(parsed.notes).toEqual([{ note: 60, channel: 0, startSec: 0, durationSec: 0.5, velocity: 1 }])
})

function midiTracks(tracks: number[][], format: number, division = 96): ArrayBuffer {
  const chunks = tracks.flatMap(events => [0x4d, 0x54, 0x72, 0x6b,
    (events.length >>> 24) & 255, (events.length >>> 16) & 255, (events.length >>> 8) & 255, events.length & 255, ...events])
  return new Uint8Array([0x4d, 0x54, 0x68, 0x64, 0, 0, 0, 6, 0, format,
    (tracks.length >>> 8) & 255, tracks.length & 255, (division >>> 8) & 255, division & 255, ...chunks]).buffer
}

function tempo(delta: number, microsecondsPerBeat: number): number[] {
  return [delta, 0xff, 0x51, 3, (microsecondsPerBeat >>> 16) & 255, (microsecondsPerBeat >>> 8) & 255, microsecondsPerBeat & 255]
}

it('integrates a format 0 note across tempo changes without rewriting elapsed time', () => {
  const parsed = parseMidiBytes(midi([
    96, 0x90, 60, 127,
    ...tempo(96, 1_000_000),
    ...tempo(96, 250_000),
    96, 0x80, 60, 0, 0, 0xff, 0x2f, 0,
  ]))
  expect(parsed.notes).toEqual([{ note: 60, channel: 0, startSec: 0.5, durationSec: 1.75, velocity: 1 }])
  expect(parsed.durationSec).toBe(2.25)
  expect(parsed.tempoBpm).toBe(240)
})

it.each([false, true])('applies the complete format 1 tempo map with conductor last: %s', conductorLast => {
  const conductor = [...tempo(96, 1_000_000), 96, 0xff, 0x2f, 0]
  const notes = [96, 0x90, 60, 127, 96, 0x80, 60, 0, 0, 0xff, 0x2f, 0]
  const parsed = parseMidiBytes(midiTracks(conductorLast ? [notes, conductor] : [conductor, notes], 1))
  expect(parsed.notes).toEqual([{ note: 60, channel: 0, startSec: 0.5, durationSec: 1, velocity: 1 }])
  expect(parsed.durationSec).toBe(1.5)
  expect(parsed.tempoBpm).toBe(60)
})

it.each([false, true])('uses chronological tempo events across format 1 tracks, reversed: %s', reversed => {
  const firstTempo = [...tempo(0, 250_000), 0, 0xff, 0x2f, 0]
  const laterTempo = [0x81, 0x40, ...tempo(0, 1_000_000).slice(1), 0, 0xff, 0x2f, 0]
  const notes = [96, 0x90, 60, 127, 0x81, 0x40, 0x80, 60, 0, 0, 0xff, 0x2f, 0]
  const tracks = reversed ? [laterTempo, notes, firstTempo] : [firstTempo, notes, laterTempo]
  const parsed = parseMidiBytes(midiTracks(tracks, 1))
  expect(parsed.notes).toEqual([{ note: 60, channel: 0, startSec: 0.25, durationSec: 1.25, velocity: 1 }])
  expect(parsed.durationSec).toBe(1.5)
  expect(parsed.tempoBpm).toBe(60)
})

it('keeps each format 2 pattern tempo independent in the existing overlay preview', () => {
  const first = [...tempo(0, 1_000_000), 96, 0x90, 60, 127, 96, 0x80, 60, 0, 0, 0xff, 0x2f, 0]
  const second = [96, 0x90, 64, 127, 96, 0x80, 64, 0, 0, 0xff, 0x2f, 0]
  const parsed = parseMidiBytes(midiTracks([first, second], 2))
  expect(parsed.notes).toEqual([
    { note: 64, channel: 0, startSec: 0.5, durationSec: 0.5, velocity: 1 },
    { note: 60, channel: 0, startSec: 1, durationSec: 1, velocity: 1 },
  ])
  expect(parsed.durationSec).toBe(2)
})

it('applies only the final tempo event at an identical format 0 tick', () => {
  const parsed = parseMidiBytes(midi([
    0, 0x90, 60, 127, ...tempo(96, 1_000_000), ...tempo(0, 250_000),
    0, 0x80, 60, 0, 0, 0x90, 64, 127, 96, 0x80, 64, 0,
  ]))
  expect(parsed.notes).toEqual([
    { note: 60, channel: 0, startSec: 0, durationSec: 0.5, velocity: 1 },
    { note: 64, channel: 0, startSec: 0.5, durationSec: 0.25, velocity: 1 },
  ])
  expect(parsed.tempoBpm).toBe(240)
})

it.each([false, true])('rejects contradictory format 1 tempo events consistently, reversed: %s', reversed => {
  const slow = [...tempo(0, 1_000_000), 0, 0xff, 0x2f, 0]
  const fast = [...tempo(0, 250_000), 0, 0xff, 0x2f, 0]
  const notes = [96, 0x90, 60, 127, 96, 0x80, 60, 0]
  expect(parseMidiBytes(midiTracks(reversed ? [fast, notes, slow] : [slow, notes, fast], 1)))
    .toEqual({ durationSec: 0, notes: [], tempoBpm: 120 })
})

it('accepts identical format 1 tempo events from multiple tracks', () => {
  const conductor = [...tempo(0, 1_000_000), 0, 0xff, 0x2f, 0]
  const notes = [...tempo(0, 1_000_000), 96, 0x90, 60, 127, 96, 0x80, 60, 0]
  expect(parseMidiBytes(midiTracks([conductor, notes], 1)).notes)
    .toEqual([{ note: 60, channel: 0, startSec: 1, durationSec: 1, velocity: 1 }])
})

it('includes the fallback tail of a late dangling note in the piano-roll duration', () => {
  const parsed = parseMidiBytes(midi([
    ...tempo(96, 1_000_000), 0x81, 0x40, 0x90, 60, 127, 0, 0xff, 0x2f, 0,
  ]))
  expect(parsed.notes).toEqual([{ note: 60, channel: 0, startSec: 2.5, durationSec: 0.25, velocity: 1 }])
  expect(parsed.durationSec).toBe(2.75)
})

it('includes the minimum preview tail of a late zero-length note in the piano-roll duration', () => {
  const parsed = parseMidiBytes(midi([0x83, 0, 0x90, 60, 127, 0, 0x80, 60, 0, 0, 0xff, 0x2f, 0]))
  expect(parsed.notes).toEqual([{ note: 60, channel: 0, startSec: 2, durationSec: 0.05, velocity: 1 }])
  expect(parsed.durationSec).toBe(2.05)
})

it('retains the unsupported SMPTE empty-result contract', () => {
  expect(parseMidiBytes(midiTracks([[0, 0x90, 60, 127, 96, 0x80, 60, 0]], 0, 0xe728))).toEqual({ durationSec: 0, notes: [], tempoBpm: 120 })
})

function audioContext(): TestAudioContext {
  const context = TestAudioContext.instances.at(-1)
  if (!context) throw new Error('Missing context')
  return context
}

it('disconnects the complete MIDI graph on natural completion before notifying its owner', () => {
  // Reset the shared context independently of module caches between tests.
  vi.spyOn(playback, 'getSharedAudioCtx').mockReturnValue(new AudioContext())
  const ended = vi.fn(() => { expect(audioContext().nodes.filter(node => node.kind !== 'destination').every(node => node.connections.size === 0)).toBe(true) })
  playMidiNotes([{ note: 60, startSec: 0, durationSec: 0.25, velocity: 0.7, channel: 0 }], 0, ended)
  vi.advanceTimersByTime(500)
  expect(ended).toHaveBeenCalledOnce()
})

it('cleans each MIDI node even when stop throws and repeated cancellation is harmless', () => {
  vi.spyOn(playback, 'getSharedAudioCtx').mockReturnValue(new AudioContext())
  const ended = vi.fn()
  const handle = playMidiNotes([{ note: 60, startSec: 0, durationSec: 10, velocity: 0.7, channel: 0 }], 0, ended)
  const context = audioContext(), oscillator = context.nodes.find(node => node.kind === 'oscillator')
  if (!oscillator) throw new Error('Missing oscillator')
  vi.spyOn(oscillator, 'stop').mockImplementation(() => { throw new Error('Already stopped') })
  handle.stop(); handle.stop(); vi.advanceTimersByTime(20_000)
  expect(context.nodes.filter(node => node.kind !== 'destination').every(node => node.connections.size === 0)).toBe(true)
  expect(ended).not.toHaveBeenCalled()
  expect(vi.getTimerCount()).toBe(0)
})

it('reclaims a partially built MIDI graph if source scheduling throws', () => {
  vi.spyOn(playback, 'getSharedAudioCtx').mockReturnValue(new AudioContext())
  const context = audioContext(), createOscillator = context.createOscillator.bind(context)
  vi.spyOn(context, 'createOscillator').mockImplementation(() => {
    const oscillator = createOscillator()
    vi.spyOn(oscillator, 'start').mockImplementation(() => { throw new Error('Scheduling failed') })
    return oscillator
  })
  expect(() => playMidiNotes([{ note: 60, startSec: 0, durationSec: 1, velocity: 0.7, channel: 0 }])).toThrow('Scheduling failed')
  expect(context.nodes.filter(node => node.kind !== 'destination').every(node => node.connections.size === 0)).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})
