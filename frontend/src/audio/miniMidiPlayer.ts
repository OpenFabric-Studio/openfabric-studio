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

function readVarLen(data: Uint8Array, offset: { val: number }): number {
  let value = 0
  let byte: number
  do {
    byte = data[offset.val++]
    value = (value << 7) | (byte & 0x7f)
  } while (byte & 0x80 && offset.val < data.length)
  return value
}

export function parseMidiBytes(bytes: ArrayBuffer): MidiParsed {
  const data = new Uint8Array(bytes)
  if (data.length < 14) return { durationSec: 0, notes: [], tempoBpm: 120 }

  // Check MThd
  if (data[0] !== 0x4d || data[1] !== 0x54 || data[2] !== 0x68 || data[3] !== 0x64) {
    return { durationSec: 0, notes: [], tempoBpm: 120 }
  }

  const division = (data[12] << 8) | data[13]
  let currentTempoBpm = 120
  let microsecondsPerBeat = 500000

  let offset = 14
  const allNotes: MidiNote[] = []
  let maxTimeSec = 0

  while (offset + 8 <= data.length) {
    // Check MTrk
    if (data[offset] === 0x4d && data[offset + 1] === 0x54 && data[offset + 2] === 0x72 && data[offset + 3] === 0x6b) {
      const trkLength =
        (data[offset + 4] << 24) |
        (data[offset + 5] << 16) |
        (data[offset + 6] << 8) |
        data[offset + 7]
      offset += 8
      const trkEnd = Math.min(data.length, offset + trkLength)
      const offObj = { val: offset }

      let currentTicks = 0
      let runningStatus = 0
      const activeNotes = new Map<number, { note: number; startSec: number; velocity: number; channel: number }>()

      while (offObj.val < trkEnd) {
        const deltaTicks = readVarLen(data, offObj)
        currentTicks += deltaTicks
        const secPerTick = microsecondsPerBeat / 1000000 / Math.max(1, division)
        const currentSec = currentTicks * secPerTick

        let status = data[offObj.val]
        if (status >= 0x80) {
          offObj.val++
          runningStatus = status
        } else {
          status = runningStatus
        }

        const msgType = status & 0xf0
        const channel = status & 0x0f

        if (status === 0xff) {
          // Meta event
          const metaType = data[offObj.val++]
          const metaLen = readVarLen(data, offObj)
          if (metaType === 0x51 && metaLen === 3) {
            // Set Tempo
            microsecondsPerBeat =
              (data[offObj.val] << 16) | (data[offObj.val + 1] << 8) | data[offObj.val + 2]
            currentTempoBpm = Math.round(60000000 / microsecondsPerBeat)
          }
          offObj.val += metaLen
        } else if (status === 0xf0 || status === 0xf7) {
          // SysEx
          const sysLen = readVarLen(data, offObj)
          offObj.val += sysLen
        } else if (msgType === 0x90) {
          // Note On
          const note = data[offObj.val++]
          const vel = data[offObj.val++]
          const key = (channel << 8) | note
          if (vel > 0) {
            activeNotes.set(key, { note, startSec: currentSec, velocity: vel / 127, channel })
          } else {
            const existing = activeNotes.get(key)
            if (existing) {
              const dur = Math.max(0.05, currentSec - existing.startSec)
              allNotes.push({ ...existing, durationSec: dur })
              activeNotes.delete(key)
              if (currentSec > maxTimeSec) maxTimeSec = currentSec
            }
          }
        } else if (msgType === 0x80) {
          // Note Off
          const note = data[offObj.val++]
          offObj.val++ // ignore release velocity
          const key = (channel << 8) | note
          const existing = activeNotes.get(key)
          if (existing) {
            const dur = Math.max(0.05, currentSec - existing.startSec)
            allNotes.push({ ...existing, durationSec: dur })
            activeNotes.delete(key)
            if (currentSec > maxTimeSec) maxTimeSec = currentSec
          }
        } else if (msgType === 0xa0 || msgType === 0xb0 || msgType === 0xe0) {
          offObj.val += 2
        } else if (msgType === 0xc0 || msgType === 0xd0) {
          offObj.val += 1
        }
      }

      // Close dangling notes
      for (const [, dangling] of activeNotes) {
        allNotes.push({ ...dangling, durationSec: 0.25 })
      }

      offset = trkEnd
    } else {
      offset++
    }
  }

  allNotes.sort((a, b) => a.startSec - b.startSec)
  return {
    durationSec: Math.max(1, maxTimeSec),
    notes: allNotes,
    tempoBpm: currentTempoBpm,
  }
}

export interface MidiSynthHandle {
  stop(): void
}

export function playMidiNotes(notes: MidiNote[], startOffsetSec = 0, onEnded?: () => void): MidiSynthHandle {
  const ctx = getSharedAudioCtx()
  const masterGain = ctx.createGain()
  masterGain.gain.setValueAtTime(0.3, ctx.currentTime)
  masterGain.connect(ctx.destination)

  const activeNodes: { osc: OscillatorNode; gain: GainNode }[] = []
  let stopped = false

  const startTime = ctx.currentTime
  let latestEnd = 0

  // Filter notes that start after or at startOffsetSec
  for (const n of notes) {
    if (n.startSec + n.durationSec <= startOffsetSec) continue
    const noteStart = Math.max(0, n.startSec - startOffsetSec)
    const noteDuration = n.durationSec - Math.max(0, startOffsetSec - n.startSec)
    if (noteDuration <= 0) continue

    const when = startTime + noteStart
    const freq = 440 * Math.pow(2, (n.note - 69) / 12)

    const osc = ctx.createOscillator()
    osc.type = n.channel === 9 ? 'square' : n.note < 48 ? 'triangle' : 'sine'
    osc.frequency.setValueAtTime(freq, when)

    const gain = ctx.createGain()
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

    activeNodes.push({ osc, gain })

    if (when + noteDuration > latestEnd) {
      latestEnd = when + noteDuration
    }
  }

  const endTimer = setTimeout(() => {
    if (!stopped && onEnded) onEnded()
  }, Math.max(0, (latestEnd - startTime) * 1000 + 100))

  return {
    stop() {
      stopped = true
      clearTimeout(endTimer)
      try {
        masterGain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.05)
        setTimeout(() => {
          for (const node of activeNodes) {
            try {
              node.osc.stop()
              node.osc.disconnect()
              node.gain.disconnect()
            } catch {}
          }
          masterGain.disconnect()
        }, 60)
      } catch {}
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
