import { getSharedAudioCtx } from '../composables/audioPlayback'
import { i18n } from '../i18n'

export interface MidiNote {
  note: number
  startSec: number
  durationSec: number
  velocity: number
  channel: number
}

export interface MidiParsed {
  durationSec: number
  notes: MidiNote[]
  tempoBpm: number
}

interface MidiCursor { val: number; end: number }
interface MidiTempoEvent { ticks: number; microsecondsPerBeat: number }
interface MidiTempoSegment extends MidiTempoEvent { startSec: number }
interface MidiTickNote {
  note: number
  startTicks: number
  endTicks: number | null
  velocity: number
  channel: number
}
interface MidiTrack { notes: MidiTickNote[]; tempos: MidiTempoEvent[] }

function buildTempoMap(events: MidiTempoEvent[], division: number): MidiTempoSegment[] {
  const segments: MidiTempoSegment[] = [{ ticks: 0, startSec: 0, microsecondsPerBeat: 500_000 }]
  for (const event of [...events].sort((a, b) => a.ticks - b.ticks)) {
    const previous = segments[segments.length - 1]
    const startSec = previous.startSec + (event.ticks - previous.ticks) * previous.microsecondsPerBeat / 1_000_000 / division
    segments.push({ ...event, startSec })
  }
  return segments
}

function buildSharedTempoMap(tracks: MidiTrack[], division: number): MidiTempoSegment[] | null {
  // SMF 1.0 puts the format 1 conductor tempo map in the first track:
  // https://midi.org/standard-midi-files-specification
  // Preserve this preview's tolerance of tempo events in other tracks by
  // merging them chronologically, provided they describe a consistent clock.
  const temposByTick = new Map<number, number>()
  for (const track of tracks) {
    for (let index = 0; index < track.tempos.length; index++) {
      const event = track.tempos[index]
      if (track.tempos[index + 1]?.ticks === event.ticks) continue
      const existing = temposByTick.get(event.ticks)
      // Contradictory tempos from different tracks have no shared clock.
      // Reject them rather than letting chunk order choose playback speed.
      if (existing !== undefined && existing !== event.microsecondsPerBeat) return null
      temposByTick.set(event.ticks, event.microsecondsPerBeat)
    }
  }
  return buildTempoMap([...temposByTick].map(([ticks, microsecondsPerBeat]) => ({ ticks, microsecondsPerBeat })), division)
}

function ticksToSeconds(ticks: number, segments: MidiTempoSegment[], division: number): number {
  // Find the last tempo at or before this tick; repeated events at the same
  // tick take effect in their original event order, without advancing time.
  let low = 0, high = segments.length - 1
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (segments[middle].ticks <= ticks) low = middle
    else high = middle - 1
  }
  const segment = segments[low]
  return segment.startSec + (ticks - segment.ticks) * segment.microsecondsPerBeat / 1_000_000 / division
}

function readByte(data: Uint8Array, cursor: MidiCursor): number {
  if (cursor.val >= cursor.end) throw new TypeError('Truncated MIDI event')
  return data[cursor.val++]
}

function readDataByte(data: Uint8Array, cursor: MidiCursor): number {
  const byte = readByte(data, cursor)
  if (byte > 127) throw new TypeError('Invalid MIDI data byte')
  return byte
}

function readVarLen(data: Uint8Array, cursor: MidiCursor): number {
  let value = 0
  for (let i = 0; i < 4; i++) {
    const byte = readByte(data, cursor)
    value = value * 128 + (byte & 0x7f)
    if (!(byte & 0x80)) return value
  }
  throw new TypeError('Invalid MIDI variable length')
}

/**
 * Bounded metrical-time MIDI preview with shared tempo maps for formats 0/1.
 * Format 2 retains the flat overlay preview, with independently timed patterns;
 * this interface does not express a pattern playlist. SMPTE is unsupported.
 */
export function parseMidiBytes(bytes: ArrayBuffer): MidiParsed {
  const empty = (): MidiParsed => ({ durationSec: 0, notes: [], tempoBpm: 120 })
  const data = new Uint8Array(bytes)
  if (data.length < 14 || data[0] !== 0x4d || data[1] !== 0x54 || data[2] !== 0x68 || data[3] !== 0x64) return empty()
  const view = new DataView(bytes)
  const headerLength = view.getUint32(4)
  const format = view.getUint16(8)
  const trackCount = view.getUint16(10)
  const division = view.getUint16(12)
  if (headerLength < 6 || headerLength > data.length - 8 || format > 2 || !trackCount || !division || division & 0x8000) return empty()

  let offset = 8 + headerLength
  const tracks: MidiTrack[] = []
  try {
    while (offset < data.length) {
      if (data.length - offset < 8) throw new TypeError('Truncated MIDI chunk')
      // Unsigned length and an explicit remaining-byte check guarantee that
      // every chunk advances; a corrupt length can never seek backwards.
      const length = view.getUint32(offset + 4)
      const start = offset + 8
      if (length > data.length - start) throw new TypeError('Truncated MIDI chunk')
      const end = start + length
      const isTrack = data[offset] === 0x4d && data[offset + 1] === 0x54 && data[offset + 2] === 0x72 && data[offset + 3] === 0x6b
      offset = end
      if (!isTrack) continue
      const track: MidiTrack = { notes: [], tempos: [] }
      tracks.push(track)
      const cursor: MidiCursor = { val: start, end }
      let currentTicks = 0, runningStatus = 0
      const activeNotes = new Map<number, MidiTickNote>()
      while (cursor.val < end) {
        currentTicks += readVarLen(data, cursor)
        let status = data[cursor.val]
        if (status >= 0x80) {
          readByte(data, cursor)
          if (status < 0xf0) runningStatus = status
        } else {
          if (!runningStatus) throw new TypeError('Missing MIDI running status')
          status = runningStatus
        }
        if (status === 0xff || status === 0xf0 || status === 0xf7) {
          const metaType = status === 0xff ? readByte(data, cursor) : undefined
          const payloadLength = readVarLen(data, cursor)
          if (payloadLength > end - cursor.val) throw new TypeError('Truncated MIDI payload')
          if (metaType === 0x51) {
            if (payloadLength !== 3) throw new TypeError('Invalid MIDI tempo')
            const microsecondsPerBeat = data[cursor.val] * 65536 + data[cursor.val + 1] * 256 + data[cursor.val + 2]
            if (!microsecondsPerBeat) throw new TypeError('Invalid MIDI tempo')
            track.tempos.push({ ticks: currentTicks, microsecondsPerBeat })
          }
          cursor.val += payloadLength
          if (metaType === 0x2f) {
            if (payloadLength !== 0) throw new TypeError('Invalid MIDI end event')
            break
          }
          continue
        }
        const msgType = status & 0xf0, channel = status & 0x0f
        if (msgType < 0x80 || msgType > 0xe0) throw new TypeError('Invalid MIDI status')
        const first = readDataByte(data, cursor)
        const second = msgType === 0xc0 || msgType === 0xd0 ? 0 : readDataByte(data, cursor)
        if (msgType === 0x90 && second > 0) {
          activeNotes.set((channel << 8) | first, { note: first, startTicks: currentTicks, endTicks: null, velocity: second / 127, channel })
        } else if (msgType === 0x80 || msgType === 0x90) {
          const key = (channel << 8) | first, existing = activeNotes.get(key)
          if (existing) {
            track.notes.push({ ...existing, endTicks: currentTicks })
            activeNotes.delete(key)
          }
        }
      }
      for (const dangling of activeNotes.values()) track.notes.push(dangling)
    }
    if (tracks.length !== trackCount) return empty()
  } catch {
    return empty()
  }
  // Parse all tick events before interpreting time: a conductor track can
  // describe changes that affect notes from any other format 0/1 track.
  const sharedTempoMap = format === 2 ? null : buildSharedTempoMap(tracks, division)
  if (format !== 2 && sharedTempoMap === null) return empty()
  const allNotes: MidiNote[] = []
  let maxTimeSec = 0, finalMicrosecondsPerBeat = 500_000
  for (const track of tracks) {
    const tempoMap = sharedTempoMap ?? buildTempoMap(track.tempos, division)
    finalMicrosecondsPerBeat = tempoMap[tempoMap.length - 1].microsecondsPerBeat
    for (const note of track.notes) {
      const startSec = ticksToSeconds(note.startTicks, tempoMap, division)
      const endSec = note.endTicks === null ? startSec + 0.25 : ticksToSeconds(note.endTicks, tempoMap, division)
      const durationSec = note.endTicks === null ? 0.25 : Math.max(0.05, endSec - startSec)
      allNotes.push({ note: note.note, channel: note.channel, velocity: note.velocity, startSec, durationSec })
      // The roll uses this duration as its horizontal extent, including the
      // audible fallback/minimum tails rather than just raw note-off ticks.
      maxTimeSec = Math.max(maxTimeSec, startSec + durationSec)
    }
  }
  allNotes.sort((a, b) => a.startSec - b.startSec)
  return { durationSec: Math.max(1, maxTimeSec), notes: allNotes, tempoBpm: Math.round(60_000_000 / finalMicrosecondsPerBeat) }
}

export interface MidiSynthHandle {
  stop(): void
}

export function playMidiNotes(notes: MidiNote[], startOffsetSec = 0, onEnded?: () => void): MidiSynthHandle {
  const ctx = getSharedAudioCtx()
  const masterGain = ctx.createGain()

  const activeNodes: AudioNode[] = [masterGain]
  const oscillators: OscillatorNode[] = []
  let stopped = false
  let disposed = false
  let fadeTimer: ReturnType<typeof setTimeout> | undefined
  function dispose(): void {
    if (disposed) return
    disposed = true
    if (fadeTimer !== undefined) clearTimeout(fadeTimer)
    for (const oscillator of oscillators) {
      try { oscillator.stop() } catch { /* Already ended sources still need disconnecting. */ }
    }
    for (const node of activeNodes) {
      try { node.disconnect() } catch { /* A failed node cannot prevent remaining cleanup. */ }
    }
  }

  const startTime = ctx.currentTime
  let latestEnd = 0

  try {
    masterGain.gain.setValueAtTime(0.3, ctx.currentTime)
    masterGain.connect(ctx.destination)
    // Filter notes that start after or at startOffsetSec
    for (const n of notes) {
      if (n.startSec + n.durationSec <= startOffsetSec) continue
      const noteStart = Math.max(0, n.startSec - startOffsetSec)
      const noteDuration = n.durationSec - Math.max(0, startOffsetSec - n.startSec)
      if (noteDuration <= 0) continue

      const when = startTime + noteStart
      const freq = 440 * Math.pow(2, (n.note - 69) / 12)

      const osc = ctx.createOscillator()
      oscillators.push(osc)
      activeNodes.push(osc)
      osc.type = n.channel === 9 ? 'square' : n.note < 48 ? 'triangle' : 'sine'
      osc.frequency.setValueAtTime(freq, when)

      const gain = ctx.createGain()
      activeNodes.push(gain)
      const peakVol = Math.max(0.05, Math.min(0.8, n.velocity * 0.4))
      const attack = 0.01
      const release = Math.min(0.05, noteDuration)

      gain.gain.setValueAtTime(0, when)
      gain.gain.linearRampToValueAtTime(peakVol, when + attack)
      gain.gain.setValueAtTime(peakVol, Math.max(when + attack, when + noteDuration - release))
      gain.gain.linearRampToValueAtTime(0, when + noteDuration)

      osc.connect(gain)
      gain.connect(masterGain)

      osc.start(when)
      osc.stop(when + noteDuration + 0.05)

      if (when + noteDuration > latestEnd) {
        latestEnd = when + noteDuration
      }
    }
  } catch (error) {
    dispose()
    throw error
  }

  const endTimer = setTimeout(() => {
    if (stopped) return
    dispose()
    onEnded?.()
  }, Math.max(0, (latestEnd - startTime) * 1000 + 100))

  return {
    stop() {
      if (stopped || disposed) return
      stopped = true
      clearTimeout(endTimer)
      try {
        masterGain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.05)
        fadeTimer = setTimeout(dispose, 60)
      } catch { dispose() }
    },
  }
}

export function drawPianoRoll(
  canvas: HTMLCanvasElement,
  notes: MidiNote[],
  totalDuration: number,
  playheadSec?: number,
) {
  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  const width = Math.max(1, rect.width)
  const height = Math.max(1, rect.height)

  canvas.width = width * dpr
  canvas.height = height * dpr

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = '#161922'
  ctx.fillRect(0, 0, width, height)

  if (notes.length === 0) {
    ctx.fillStyle = '#64748b'
    ctx.font = '11px sans-serif'
    ctx.fillText(i18n.global.t('storeErrors.notesNotFound'), 10, height / 2)
    return
  }

  let minNote = 127
  let maxNote = 0
  for (const n of notes) {
    if (n.note < minNote) minNote = n.note
    if (n.note > maxNote) maxNote = n.note
  }
  minNote = Math.max(0, minNote - 2)
  maxNote = Math.min(127, maxNote + 2)
  const noteRange = Math.max(12, maxNote - minNote + 1)

  // Grid lines
  ctx.strokeStyle = '#232936'
  ctx.lineWidth = 1
  for (let n = minNote; n <= maxNote; n++) {
    if (n % 12 === 0) {
      const y = height - ((n - minNote) / noteRange) * height
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.lineTo(width, y)
      ctx.stroke()
    }
  }

  // Draw notes
  const dur = Math.max(1, totalDuration)
  for (const n of notes) {
    const x = (n.startSec / dur) * width
    const w = Math.max(2, (n.durationSec / dur) * width)
    const y = height - ((n.note - minNote + 1) / noteRange) * height
    const h = Math.max(2, height / noteRange - 1)

    // Color based on pitch
    const hue = ((n.note * 23) % 360)
    ctx.fillStyle = `hsl(${hue}, 80%, 60%)`
    ctx.beginPath()
    ctx.roundRect(x, y, w, h, 1)
    ctx.fill()
  }

  // Playhead line
  if (playheadSec != null && playheadSec >= 0) {
    const px = (playheadSec / dur) * width
    ctx.strokeStyle = '#ef4444'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(px, 0)
    ctx.lineTo(px, height)
    ctx.stroke()
  }
}
