import { apiFetch, ApiError } from './http'
import {
  parseSpeechVoiceProfile,
  parseSpeechVoiceProfilesResponse,
  parseSpeechCloneTrialResponse,
  parsePatchSpeechVoiceProfileRequest,
} from './contracts'
import type {
  SpeechVoiceProfile,
  SpeechVoiceProfilesResponse,
  SpeechCloneTrialResponse,
  PatchSpeechVoiceProfileRequest,
} from './contracts'

export type { SpeechVoiceProfile, SpeechCloneTrialResponse }

export async function listSpeechVoiceProfiles(signal?: AbortSignal): Promise<SpeechVoiceProfile[]> {
  const json = await apiFetch('/api/voice-profiles', { signal }, parseSpeechVoiceProfilesResponse)
  return json.profiles
}

export async function createSpeechVoiceProfile(input: {
  name: string
  consentConfirmed: boolean
  audio: File
  notes?: string
}, signal?: AbortSignal): Promise<SpeechVoiceProfile> {
  const form = new FormData()
  form.append('name', input.name)
  form.append('consent_confirmed', input.consentConfirmed ? 'true' : 'false')
  if (input.notes) form.append('notes', input.notes)
  form.append('audio', input.audio, input.audio.name)
  return apiFetch('/api/voice-profiles', { method: 'POST', body: form, signal }, parseSpeechVoiceProfile)
}

export async function deleteSpeechVoiceProfile(profileId: string, signal?: AbortSignal): Promise<void> {
  await apiFetch(`/api/voice-profiles/${encodeURIComponent(profileId)}`, { method: 'DELETE', signal })
}

export async function patchSpeechVoiceProfile(
  profileId: string,
  body: PatchSpeechVoiceProfileRequest,
  signal?: AbortSignal,
): Promise<SpeechVoiceProfile> {
  const validated = parsePatchSpeechVoiceProfileRequest(body)
  return apiFetch(`/api/voice-profiles/${encodeURIComponent(profileId)}`, {
    method: 'PATCH',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(validated),
  }, parseSpeechVoiceProfile)
}

export async function startSpeechCloneTrial(
  profileId: string,
  text: string,
  signal?: AbortSignal,
): Promise<SpeechCloneTrialResponse> {
  // Scaffold returns HTTP 501 with a structured body when GPT-SoVITS is absent.
  const resp = await fetch('/api/speech-clone/trials', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile_id: profileId, text, engine: 'gpt-sovits' }),
  })
  const raw: unknown = await resp.json().catch(() => undefined)
  if (resp.status === 501) return parseSpeechCloneTrialResponse(raw)
  if (!resp.ok) {
    const detail = (raw && typeof raw === 'object' && 'detail' in raw && typeof (raw as { detail: unknown }).detail === 'string')
      ? (raw as { detail: string }).detail
      : resp.statusText
    throw new ApiError(detail || `HTTP ${resp.status}`, resp.status)
  }
  return parseSpeechCloneTrialResponse(raw)
}

export type { SpeechVoiceProfilesResponse }
