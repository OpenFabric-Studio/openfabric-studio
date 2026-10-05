import { apiFetch, ApiError } from './http'
import {
  parseSpeechVoiceProfile,
  parseSpeechVoiceProfilesResponse,
  parseSpeechCloneTrialResponse,
  parseSpeechCloneEngineStatus,
  parsePatchSpeechVoiceProfileRequest,
  parseStarterSpeechVoicesResponse,
  parseSpeechCloneTrialRequest,
} from './contracts'
import type {
  SpeechVoiceProfile,
  SpeechVoiceProfilesResponse,
  SpeechCloneTrialResponse,
  SpeechCloneEngineStatus,
  PatchSpeechVoiceProfileRequest,
  StarterSpeechVoice,
  CloudSpeechApproval,
} from './contracts'

export type { SpeechVoiceProfile, SpeechCloneTrialResponse, SpeechCloneEngineStatus, StarterSpeechVoice }

export async function listStarterSpeechVoices(signal?: AbortSignal): Promise<StarterSpeechVoice[]> {
  const response = await apiFetch('/api/voice-profiles/starter-voices', { signal }, parseStarterSpeechVoicesResponse)
  return response.voices
}

export async function importStarterSpeechVoice(id: string, signal?: AbortSignal): Promise<SpeechVoiceProfile> {
  if (!/^vctk-p[0-9]{3}$/.test(id)) throw new TypeError('Invalid starter voice identifier')
  return apiFetch(`/api/voice-profiles/starter-voices/${id}/import`, { method: 'POST', signal }, parseSpeechVoiceProfile)
}

export function speechTrialAudioUrl(id: SpeechCloneTrialResponse['trial_id']): string | null {
  return typeof id === 'string' && /^[0-9a-f]{32}$/.test(id) ? `/api/speech-clone/trials/${id}/audio` : null
}

export async function listSpeechVoiceProfiles(signal?: AbortSignal): Promise<SpeechVoiceProfile[]> {
  const json = await apiFetch('/api/voice-profiles', { signal }, parseSpeechVoiceProfilesResponse)
  return json.profiles
}

export async function createSpeechVoiceProfile(input: {
  name: string
  consentConfirmed: boolean
  audio: File
  notes?: string
  referenceTranscript?: string
  referenceLanguage?: string
}, signal?: AbortSignal): Promise<SpeechVoiceProfile> {
  const form = new FormData()
  form.append('name', input.name)
  form.append('consent_confirmed', input.consentConfirmed ? 'true' : 'false')
  if (input.notes) form.append('notes', input.notes)
  if (input.referenceTranscript !== undefined) form.append('reference_transcript', input.referenceTranscript)
  if (input.referenceLanguage !== undefined) form.append('reference_language', input.referenceLanguage)
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

export async function getSpeechCloneEngine(signal?: AbortSignal): Promise<SpeechCloneEngineStatus> {
  return apiFetch('/api/speech-clone/engine', { signal }, parseSpeechCloneEngineStatus)
}

export async function startSpeechCloneTrial(
  profileId: string,
  text: string,
  signal?: AbortSignal,
  textLanguage?: string,
  cloudApproval?: CloudSpeechApproval | null,
): Promise<SpeechCloneTrialResponse> {
  // Missing engine returns HTTP 501 with a structured body; mock/ready return 200.
  const resp = await fetch('/api/speech-clone/trials', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parseSpeechCloneTrialRequest({ profile_id: profileId, text, engine: 'gpt-sovits', ...(textLanguage ? { text_language: textLanguage } : {}), ...(cloudApproval ? { cloud_approval: cloudApproval } : {}) })),
  })
  const raw: unknown = await resp.json().catch(() => undefined)
  if (resp.status === 501) return parseSpeechCloneTrialResponse(raw)
  if (!resp.ok) {
    const detail = (raw && typeof raw === 'object' && 'detail' in raw && typeof raw.detail === 'string')
      ? raw.detail
      : resp.statusText
    throw new ApiError(detail || `HTTP ${resp.status}`, resp.status)
  }
  return parseSpeechCloneTrialResponse(raw)
}

export type { SpeechVoiceProfilesResponse }
