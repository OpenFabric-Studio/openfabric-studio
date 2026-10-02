import type { MidiNote } from './miniMidiPlayer'

export function parseAbcToMidi(abc: string, defaultBpm: number = 120): MidiNote[] {
  const lines = abc.split('\n')
  
  let currentBpm = defaultBpm
  // Default unit note length is often 1/8 in ABC if not specified
  let defaultNoteLength = 1 / 8 
  
  let inHeader = true
  const scoreLines: string[] = []
  
  for (const line of lines) {
    const t = line.trim()
    if (!t) continue
    if (t.startsWith('%')) continue // comment
    
    if (inHeader && /^[A-Z]:/.test(t)) {
      const type = t.charAt(0)
      const value = t.substring(2).trim()
      
      if (type === 'L') {
        // e.g. L: 1/8
        const match = value.match(/(\d+)\/(\d+)/)
        if (match) {
          defaultNoteLength = parseInt(match[1], 10) / parseInt(match[2], 10)
        } else if (value === '1') {
          defaultNoteLength = 1
        }
      } else if (type === 'Q') {
        // e.g. Q: 120 or Q: 1/4=120
        const match = value.match(/(?:(\d+)\/(\d+)=)?(\d+)/)
        if (match) {
          if (match[1] && match[2]) {
            // we could scale bpm based on reference length vs L, but let's keep it simple
            currentBpm = parseInt(match[3], 10)
          } else {
            currentBpm = parseInt(match[3], 10)
          }
        }
      } else if (type === 'K') {
        inHeader = false // end of header typically
      }
    } else {
      inHeader = false
      scoreLines.push(t)
    }
  }

  const scoreStr = scoreLines.join(' ')
  const notes: MidiNote[] = []
  
  // Note parsing regex
  // 1: accidentals (^, _, =)
  // 2: note letter (A-G, a-g, z)
  // 3: octave (', ,)
  // 4: length fraction (e.g. 2, /2, 3/4)
  const regex = /([\^_=]*)([a-gA-Gz])([',]*)([\d]*\/?[\d]*)/g
  let match
  
  let currentSec = 0
  const bps = currentBpm / 60
  // duration of a whole note in seconds
  // if bpm is quarter notes per minute, then 1 quarter note = 1 beat
  // a whole note is 4 quarter notes = 4 beats.
  // 1 beat = 1 / bps seconds.
  // whole note = 4 / bps seconds.
  const wholeNoteSec = 4 / bps

  // Basic key signature accidentals could go here, but we assume explicit accidentals or C major for now.
  const basePitches: Record<string, number> = {
    'C': 60, 'D': 62, 'E': 64, 'F': 65, 'G': 67, 'A': 69, 'B': 71,
    'c': 72, 'd': 74, 'e': 76, 'f': 77, 'g': 79, 'a': 81, 'b': 83,
  }

  while ((match = regex.exec(scoreStr)) !== null) {
    const accidentals = match[1]
    const letter = match[2]
    const octaves = match[3]
    const lengthStr = match[4]

    let lenMult = 1
    if (lengthStr) {
      if (lengthStr === '/') lenMult = 0.5
      else if (lengthStr.startsWith('/')) {
        const den = parseInt(lengthStr.substring(1), 10)
        if (den) lenMult = 1 / den
      } else if (lengthStr.includes('/')) {
        const parts = lengthStr.split('/')
        const num = parseInt(parts[0], 10) || 1
        const den = parseInt(parts[1], 10) || 1
        lenMult = num / den
      } else {
        lenMult = parseInt(lengthStr, 10) || 1
      }
    }

    const noteDurationSec = defaultNoteLength * lenMult * wholeNoteSec

    if (letter === 'z') {
      // rest
      currentSec += noteDurationSec
      continue
    }

    let pitch = basePitches[letter]
    if (pitch === undefined) continue

    // Accidentals
    for (const acc of accidentals) {
      if (acc === '^') pitch += 1
      else if (acc === '_') pitch -= 1
    }

    // Octaves
    for (const oct of octaves) {
      if (oct === "'") pitch += 12
      else if (oct === ",") pitch -= 12
    }

    notes.push({
      note: pitch,
      startSec: currentSec,
      durationSec: noteDurationSec,
      velocity: 100,
      channel: 0
    })

    currentSec += noteDurationSec
  }

  return notes
}
