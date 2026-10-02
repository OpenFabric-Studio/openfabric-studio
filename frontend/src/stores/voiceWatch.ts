import type { ApplyStatus } from '../api/voices'
import { rememberVoiceClock, voiceClock } from './voiceClock'
import type { VoiceJobProgress } from '../api/contracts'

const watching = new Set<number>()

export function voiceWatchActive(trackId: number): boolean {
  return watching.has(trackId)
}

export function markVoiceWatch(trackId: number) {
  watching.add(trackId)
}

export function clearVoiceWatch(trackId: number) {
  watching.delete(trackId)
}

export interface VoiceNote {
  voicePhase: string
  voiceId: string
  voiceName: string
  voiceStartedAt: number
  voiceProgress: VoiceJobProgress | null
}

export function noteVoice(trackId: number, row: ApplyStatus): VoiceNote {
  const clock = voiceClock(trackId)
  const voiceId = row.voice_id || clock?.voiceId || ''
  const voiceName = row.voice_name || clock?.voiceName || ''
  let startedAt = row.started_at && row.started_at > 0 ? row.started_at * 1000 : (clock?.startedAt || 0)
  if (!startedAt) startedAt = Date.now()
  if (!clock || clock.startedAt !== startedAt || clock.voiceId !== voiceId || clock.voiceName !== voiceName) {
    rememberVoiceClock(trackId, { startedAt, voiceId, voiceName })
  }
  return { voicePhase: row.phase || '', voiceId, voiceName, voiceStartedAt: startedAt, voiceProgress: row.job_progress ?? null }
}
