import { isRecord } from '../api/nativeValidation'

const CLOCK_KEY = 'openfabric_voice_clock'

export interface VoiceClock {
  startedAt: number
  voiceId: string
  voiceName: string
}

function readClocks(): Record<string, VoiceClock> {
  try {
    const raw = localStorage.getItem(CLOCK_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!isRecord(parsed)) return {}
    const clocks: Record<string, VoiceClock> = {}
    for (const [key, row] of Object.entries(parsed)) {
      if (!isRecord(row) || typeof row.startedAt !== 'number' || !Number.isFinite(row.startedAt) || row.startedAt <= 0) continue
      if (typeof row.voiceId !== 'string' || typeof row.voiceName !== 'string') continue
      clocks[key] = { startedAt: row.startedAt, voiceId: row.voiceId, voiceName: row.voiceName }
    }
    return clocks
  } catch {
    return {}
  }
}

function writeClocks(clocks: Record<string, VoiceClock>) {
  try {
    localStorage.setItem(CLOCK_KEY, JSON.stringify(clocks))
  } catch {
    // A private window can refuse storage. The open page still shows the clock.
  }
}

export function voiceClock(trackId: number): VoiceClock | null {
  const row = readClocks()[String(trackId)]
  if (!row || typeof row.startedAt !== 'number' || row.startedAt <= 0) return null
  return row
}

export function rememberVoiceClock(trackId: number, clock: VoiceClock) {
  const all = readClocks()
  all[String(trackId)] = clock
  writeClocks(all)
}

export function forgetVoiceClock(trackId: number) {
  const all = readClocks()
  delete all[String(trackId)]
  writeClocks(all)
}

const USED_KEY = 'openfabric_voice_used'

interface UsedVoice { voiceId: string; voiceName: string }

function readUsed(): Record<string, UsedVoice> {
  try {
    const raw = localStorage.getItem(USED_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!isRecord(parsed)) return {}
    const used: Record<string, UsedVoice> = {}
    for (const [key, row] of Object.entries(parsed)) {
      if (!isRecord(row)) continue
      if (row.voiceId !== undefined && typeof row.voiceId !== 'string') continue
      if (row.voiceName !== undefined && typeof row.voiceName !== 'string') continue
      used[key] = { voiceId: typeof row.voiceId === 'string' ? row.voiceId : '', voiceName: typeof row.voiceName === 'string' ? row.voiceName : '' }
    }
    return used
  } catch { return {} }
}

export function rememberVoiceUsed(trackId: number, voiceId: string, voiceName: string) {
  if (!voiceId && !voiceName) return
  try {
    const used = readUsed()
    used[String(trackId)] = { voiceId, voiceName }
    localStorage.setItem(USED_KEY, JSON.stringify(used))
  } catch {
    // The open page still has the name on the card.
  }
}

export function voiceUsed(trackId: number): UsedVoice | null {
  return readUsed()[String(trackId)] || null
}
