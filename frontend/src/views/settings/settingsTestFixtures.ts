import type { CompleteAudioEncodingSettings, CompleteAudioSettingsResponse } from '../../api/audioSettings'

export function encodingSettings(): CompleteAudioEncodingSettings {
  return {
    mp3: { mode: 'cbr', bitrate_kbps: 320, vbr_quality: 2, sample_rate: 48000, channels: 2 },
    wav: { bit_depth: 24, sample_rate: 48000, channels: 2 },
    flac: { bit_depth: 24, compression_level: 5, sample_rate: 48000, channels: 2 },
  }
}
export function audioSettingsResponse(): CompleteAudioSettingsResponse {
  return { settings: encodingSettings(), defaults: encodingSettings() }
}
