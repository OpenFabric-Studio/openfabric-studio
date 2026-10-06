import type { AudioEncodingSettings, AudioSettingsResponse } from './contracts'
import { parseAudioEncodingSettings, parseAudioSettingsResponse } from './contracts'
import { apiFetch } from './http'
export type { AudioEncodingSettings, AudioSettingsResponse } from './contracts'

export type CompleteAudioEncodingSettings = {
  [Format in keyof Required<AudioEncodingSettings>]: Required<NonNullable<AudioEncodingSettings[Format]>>
}
export type CompleteAudioSettingsResponse = Omit<AudioSettingsResponse, 'settings' | 'defaults'> & {
  settings: CompleteAudioEncodingSettings
  defaults: CompleteAudioEncodingSettings
}

function completeSettings(value: AudioEncodingSettings): CompleteAudioEncodingSettings {
  const { mp3, wav, flac, loudness } = value
  if (!mp3 || !wav || !flac || !loudness || loudness.profile === undefined || loudness.integrated_lufs === undefined || loudness.true_peak_dbtp === undefined || mp3.mode === undefined || mp3.bitrate_kbps === undefined || mp3.vbr_quality === undefined || mp3.sample_rate === undefined || mp3.channels === undefined || wav.bit_depth === undefined || wav.sample_rate === undefined || wav.channels === undefined || flac.bit_depth === undefined || flac.compression_level === undefined || flac.sample_rate === undefined || flac.channels === undefined) {
    throw new TypeError('Incomplete audio settings response')
  }
  return {
    mp3: { mode: mp3.mode, bitrate_kbps: mp3.bitrate_kbps, vbr_quality: mp3.vbr_quality, sample_rate: mp3.sample_rate, channels: mp3.channels },
    wav: { bit_depth: wav.bit_depth, sample_rate: wav.sample_rate, channels: wav.channels },
    flac: { bit_depth: flac.bit_depth, compression_level: flac.compression_level, sample_rate: flac.sample_rate, channels: flac.channels },
    loudness: { profile: loudness.profile, integrated_lufs: loudness.integrated_lufs, true_peak_dbtp: loudness.true_peak_dbtp },
  }
}

function parseCompleteResponse(value: unknown): CompleteAudioSettingsResponse {
  const response = parseAudioSettingsResponse(value)
  return { ...response, settings: completeSettings(response.settings), defaults: completeSettings(response.defaults) }
}

export function getAudioSettings(signal?: AbortSignal): Promise<CompleteAudioSettingsResponse> {
  return apiFetch('/api/settings/audio', { signal }, parseCompleteResponse)
}
export async function saveAudioSettings(settings: AudioEncodingSettings, signal?: AbortSignal): Promise<CompleteAudioSettingsResponse> {
  const validated = parseAudioEncodingSettings(settings)
  return apiFetch('/api/settings/audio', {
    method: 'PUT', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(validated),
  }, parseCompleteResponse)
}
