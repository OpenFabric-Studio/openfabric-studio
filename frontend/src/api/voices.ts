import { apiFetch, ApiError } from './http'
import { i18n } from '../i18n'

import {
  parseVoicesResponse, parseVoiceProfileResponse, parseVoiceUploadResponse, parseApplyStatusResponse,
  parsePrepareVoiceRequest, parseVoicePreparationResponse, parseSelectVoiceSamplesRequest,
  parseBuildVoiceRequest, parseSelectVoiceModelRequest, parseVoiceTrialSource,
  parseVoiceTrialSourcesResponse, parseVoiceComparisonRequest, parseVoiceComparisonResponse,
  parseVoiceComparisonsResponse, parseVoiceTrialRating, parseVoiceSeparationOptionsResponse,
  parseAnalyzeVoiceCoverageRequest, parseApplyVoiceRequest, parseVoiceReplacementResponse,
} from './contracts'
import type { VoiceProfileResponse, ApplyStatusResponse, VoiceUploadResponse, CreateVoiceRequest, BuildVoiceRequest, VoiceReplacementResponse } from './contracts'
import type { PrepareVoiceRequest, VoicePreparationResponse, SelectVoiceSamplesRequest, VoiceComparisonRequest, VoiceComparisonResponse, VoiceTrialRating, VoiceTrialSource, VoiceSeparationOptionsResponse } from './contracts'

export type VoiceProfile = VoiceProfileResponse
export type VoiceStatus = VoiceProfile['status']
export type VoiceRecording = VoiceProfile['recordings'][number]
export type VoiceExtractReport = NonNullable<VoiceProfile['extract_report']>
export type ApplyStatus = ApplyStatusResponse

const STORAGE_KEY = 'openfabric_voice_id'
const ACTIVE: VoiceStatus[] = ['queued', 'extracting', 'cleaning', 'preparing', 'merging', 'training']

export function isVoiceActive(status: string | undefined): boolean {
  return ACTIVE.some((candidate) => candidate === status)
}

export function voiceErrorText(code: string, detail = ''): string {
  const translate = i18n.global.t
  if (code) {
    const key = `voiceClone.err.${code}`
    const message = String(translate(key))
    if (message !== key) return detail ? `${message} ${detail}` : message
  }
  return detail || code || String(translate('voiceClone.err.unknown'))
}

export function getActiveVoiceId(): string | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value && /^[0-9a-f]{32}$/.test(value) ? value : null
  } catch {
    return null
  }
}

export function setActiveVoiceId(id: string | null): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // private browsing
  }
  window.dispatchEvent(new Event('openfabric-voice'))
}

export async function listVoices(signal?: AbortSignal): Promise<VoiceProfile[]> {
  const json = await apiFetch('/api/voices', { signal }, parseVoicesResponse)
  return json.voices
}

const voiceNames = new Map<string, string>()

export async function voiceNameFor(voiceId: string): Promise<string> {
  if (!voiceId) return ''
  if (voiceNames.has(voiceId)) return voiceNames.get(voiceId) || ''
  try {
    for (const voice of await listVoices()) voiceNames.set(voice.id, voice.name)
  } catch {
    return ''
  }
  return voiceNames.get(voiceId) || ''
}

export async function createVoice(name: string, signal?: AbortSignal): Promise<VoiceProfile> {
  return apiFetch('/api/voices', {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name } satisfies CreateVoiceRequest),
  }, parseVoiceProfileResponse)
}

export async function uploadRecordings(
  voiceId: string,
  files: File[],
  signal?: AbortSignal,
): Promise<VoiceUploadResponse> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  return apiFetch(voicePath(voiceId, 'recordings'), { method: 'POST', body: form, signal }, parseVoiceUploadResponse)
}

export async function deleteVoice(voiceId: string, signal?: AbortSignal): Promise<void> {
  await apiFetch(`/api/voices/${encodeURIComponent(voiceId)}`, { method: 'DELETE', signal })
}

function voicePath(voiceId: string, suffix: string): string {
  return `/api/voices/${encodeURIComponent(voiceId)}/${suffix}`
}

function voiceJson<T>(voiceId: string, suffix: string, body: unknown, method: 'POST' | 'PATCH', decoder: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  return apiFetch(voicePath(voiceId, suffix), { method, headers: { 'Content-Type': 'application/json' }, signal, body: JSON.stringify(body) }, decoder)
}

export async function buildVoice(voiceId: string, request: BuildVoiceRequest, signal?: AbortSignal): Promise<VoiceProfile> {
  return voiceJson(voiceId, 'build', parseBuildVoiceRequest(request), 'POST', parseVoiceProfileResponse, signal)
}

export async function prepareVoice(voiceId: string, request: PrepareVoiceRequest, signal?: AbortSignal): Promise<VoicePreparationResponse> {
  return voiceJson(voiceId, 'prepare', parsePrepareVoiceRequest(request), 'POST', parseVoicePreparationResponse, signal)
}

export function getVoicePreparation(voiceId: string, signal?: AbortSignal): Promise<VoicePreparationResponse> {
  return apiFetch(voicePath(voiceId, 'preparation'), { signal }, parseVoicePreparationResponse)
}

export async function analyzeVoiceCoverage(voiceId: string, revision: string, signal?: AbortSignal): Promise<VoicePreparationResponse> {
  return voiceJson(voiceId, 'coverage', parseAnalyzeVoiceCoverageRequest({ revision }), 'POST', parseVoicePreparationResponse, signal)
}

export function cancelVoicePreparation(voiceId: string, signal?: AbortSignal): Promise<VoicePreparationResponse> {
  return voiceJson(voiceId, 'prepare/cancel', {}, 'POST', parseVoicePreparationResponse, signal)
}

export async function selectVoiceSamples(voiceId: string, request: SelectVoiceSamplesRequest, signal?: AbortSignal): Promise<VoicePreparationResponse> {
  return voiceJson(voiceId, 'selection', parseSelectVoiceSamplesRequest(request), 'PATCH', parseVoicePreparationResponse, signal)
}

export async function selectVoiceModel(voiceId: string, modelId: string, signal?: AbortSignal): Promise<VoiceProfile> {
  return voiceJson(voiceId, 'model', parseSelectVoiceModelRequest({ model_id: modelId }), 'PATCH', parseVoiceProfileResponse, signal)
}

export function voiceSampleUrl(voiceId: string, segmentId: string, variant: 'original' | 'cleaned', revision?: string): string {
  return voicePath(voiceId, `samples/${encodeURIComponent(segmentId)}?variant=${variant}${revision ? `&revision=${encodeURIComponent(revision)}` : ''}`)
}

export function voiceReferenceUrl(voiceId: string, referenceId: string, revision?: string): string {
  return voicePath(voiceId, `reference/${encodeURIComponent(referenceId)}${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`)
}

export async function voiceSeparationOptions(signal?: AbortSignal): Promise<VoiceSeparationOptionsResponse['options']> {
  return (await apiFetch('/api/voices/separation-options', { signal }, parseVoiceSeparationOptionsResponse)).options
}

export function uploadVoiceTrialSource(voiceId: string, file: File, signal?: AbortSignal): Promise<VoiceTrialSource> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch(voicePath(voiceId, 'trial-sources'), { method: 'POST', body: form, signal }, parseVoiceTrialSource)
}

export async function listVoiceTrialSources(voiceId: string, signal?: AbortSignal): Promise<VoiceTrialSource[]> {
  return (await apiFetch(voicePath(voiceId, 'trial-sources'), { signal }, parseVoiceTrialSourcesResponse)).sources
}

export async function startVoiceComparison(voiceId: string, request: VoiceComparisonRequest, signal?: AbortSignal): Promise<VoiceComparisonResponse> {
  return voiceJson(voiceId, 'comparisons', parseVoiceComparisonRequest(request), 'POST', parseVoiceComparisonResponse, signal)
}

export async function listVoiceComparisons(voiceId: string, signal?: AbortSignal): Promise<VoiceComparisonResponse[]> {
  return (await apiFetch(voicePath(voiceId, 'comparisons'), { signal }, parseVoiceComparisonsResponse)).comparisons
}

export function cancelVoiceComparison(voiceId: string, comparisonId: string, signal?: AbortSignal): Promise<VoiceComparisonResponse> {
  return voiceJson(voiceId, `comparisons/${encodeURIComponent(comparisonId)}/cancel`, {}, 'POST', parseVoiceComparisonResponse, signal)
}

export async function rateVoiceTrial(voiceId: string, comparisonId: string, trialId: string, rating: VoiceTrialRating, signal?: AbortSignal): Promise<VoiceComparisonResponse> {
  return voiceJson(voiceId, `comparisons/${encodeURIComponent(comparisonId)}/trials/${encodeURIComponent(trialId)}/rating`, parseVoiceTrialRating(rating), 'PATCH', parseVoiceComparisonResponse, signal)
}

export async function cancelVoiceBuild(voiceId: string, signal?: AbortSignal): Promise<VoiceProfile> {
  return voiceJson(voiceId, 'cancel', {}, 'POST', parseVoiceProfileResponse, signal)
}

export class VoiceApplyError extends Error {
  code: string
  detail: string
  constructor(code: string, detail: string) {
    super(detail || code)
    this.name = 'VoiceApplyError'
    this.code = code
    this.detail = detail
  }
}

export function applyStatus(trackId: number, signal?: AbortSignal): Promise<ApplyStatus> {
  return apiFetch(`/api/voices/apply/${trackId}`, { signal }, parseApplyStatusResponse)
}

function validateVoiceId(voiceId: string): void {
  if (!/^[0-9a-f]{32}$/.test(voiceId)) throw new TypeError('invalid_voice_id')
}

function validateTrackId(trackId: number): void {
  if (!Number.isSafeInteger(trackId) || trackId <= 0) throw new TypeError('invalid_track_id')
}

export async function replaceVoice(file: File, voiceId: string, signal?: AbortSignal): Promise<VoiceReplacementResponse> {
  validateVoiceId(voiceId)
  signal?.throwIfAborted()
  const form = new FormData()
  form.append('audio', file, file.name)
  form.append('voice_id', voiceId)
  return apiFetch('/api/voices/replace', { method: 'POST', body: form, signal }, parseVoiceReplacementResponse)
}

export async function cancelVoiceApply(trackId: number, signal?: AbortSignal): Promise<ApplyStatus> {
  validateTrackId(trackId)
  return apiFetch(`/api/voices/apply/${trackId}/cancel`, { method: 'POST', signal }, parseApplyStatusResponse)
}

export async function applyVoice(voiceId: string, trackId: number, signal?: AbortSignal): Promise<ApplyStatus> {
  validateVoiceId(voiceId)
  validateTrackId(trackId)
  return apiFetch('/api/voices/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify(parseApplyVoiceRequest({ voice_id: voiceId, track_id: trackId })),
  }, parseApplyStatusResponse)
}

function pollDelay(signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted()
  return new Promise((resolve, reject) => {
    const aborted = () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', aborted)
      const reason: unknown = signal?.reason
      reject(reason)
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', aborted)
      resolve()
    }, 2000)
    signal?.addEventListener('abort', aborted, { once: true })
    if (signal?.aborted) aborted()
  })
}

export async function waitForVoiceApply(
  trackId: number,
  onUpdate?: (row: ApplyStatus) => void,
  signal?: AbortSignal,
): Promise<string> {
  const started = Date.now()
  while (Date.now() - started < 3 * 60 * 60 * 1000) {
    signal?.throwIfAborted()
    const row = await applyStatus(trackId, signal)
    signal?.throwIfAborted()
    if (row.status === 'done') {
      const url = row.audio_url || `/api/tracks/${trackId}/audio?v=${Date.now()}`
      onUpdate?.({ ...row, status: 'done', audio_url: url })
      return url
    }
    if (row.status === 'failed' || row.status === 'cancelled' || row.status === 'idle') {
      throw new VoiceApplyError(row.error_code || 'interrupted', row.error)
    }
    onUpdate?.(row)
    await pollDelay(signal)
  }
  throw new VoiceApplyError('', 'timed out')
}

export async function applyVoiceAndWait(
  voiceId: string,
  trackId: number,
  onUpdate?: (row: ApplyStatus) => void,
  signal?: AbortSignal,
): Promise<string> {
  try {
    signal?.throwIfAborted()
    await applyVoice(voiceId, trackId, signal)
    signal?.throwIfAborted()
  } catch (err) {
    if (err instanceof ApiError) throw new VoiceApplyError(err.message, '')
    throw err
  }
  return waitForVoiceApply(trackId, onUpdate, signal)
}
