import { apiFetch } from './http'
import { parseRecordingSessionsResponse, parseInspectRecordingRequest, parseRecordingSession } from './contracts'
import type { InspectRecordingRequest, RecordingSession } from './contracts'

function base(voiceId: string): string {
  if (!/^[0-9a-f]{32}$/.test(voiceId)) throw new TypeError('Invalid recording voice identifier')
  return `/api/voices/${voiceId}/recording-sessions`
}
function filename(value: string): void {
  if (!value || value === '.' || value === '..' || /[\\/\u0000-\u001f\u007f]/.test(value)) throw new TypeError('Invalid recording filename')
}
function owned(session: RecordingSession, voiceId: string): void {
  filename(session.original_filename)
  if (session.voice_id !== voiceId || session.relative_path.startsWith('/') || session.relative_path.includes('\\') || session.relative_path.split('/').includes('..')) throw new TypeError('Invalid recording ownership')
}
export async function listRecordingSessions(voiceId: string, signal?: AbortSignal): Promise<RecordingSession[]> {
  const response = await apiFetch(base(voiceId), { signal }, parseRecordingSessionsResponse)
  if (response.voice_id !== voiceId) throw new TypeError('Invalid recording ownership')
  const sessions = response.sessions ?? []
  for (const session of sessions) owned(session, voiceId)
  return sessions
}
export async function inspectRecording(voiceId: string, request: InspectRecordingRequest, signal?: AbortSignal): Promise<RecordingSession> {
  const url = base(voiceId), body = parseInspectRecordingRequest(request)
  filename(body.filename)
  const response = await apiFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }, parseRecordingSession)
  owned(response, voiceId)
  const relative = `${body.purpose === 'training' ? 'recordings' : 'trials/sources'}/${body.filename}`
  if (response.purpose !== body.purpose || response.relative_path !== relative) throw new TypeError('Invalid recording inspection identity')
  return response
}
