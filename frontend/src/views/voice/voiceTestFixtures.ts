import type { VoicePreparationResponse, VoiceProfileResponse, VoiceComparisonResponse } from '../../api/contracts'

export function preparedVoice(): VoicePreparationResponse {
  return { revision: 'revision-1', status: 'done', error_code: '', options: { sources: [{ filename: 'song.wav', enabled: true, kind: 'song' }], separation_quality: 'fast', clean: true, singer_confirmed: true },
    segments: [{ id: 'segment-1', source_filename: 'song.wav', start_sec: 4, end_sec: 14, duration_sec: 10, score: 0.8, periodicity: 0.7, level_db: -20, peak: 0.5, clipped_fraction: 0, accepted: true, reasons: [], has_cleaned: true },
      { id: 'segment-2', source_filename: 'song.wav', start_sec: 14, end_sec: 24, duration_sec: 10, score: 0.1, periodicity: 0.1, level_db: -60, peak: 0.001, clipped_fraction: 0, accepted: false, reasons: ['quiet'], has_cleaned: false }],
    sources: [{ filename: 'song.wav', duration_sec: 24, accepted_sec: 10, rejected_sec: 14, error_code: '' }],
    selected_segment_ids: ['segment-1'], cleaned_segment_ids: [], references: [{ id: 'reference-1', segment_id: 'segment-1', source_filename: 'song.wav', start_sec: 4, end_sec: 14, duration_sec: 10, score: 0.8 }],
    reference_id: 'reference-1', accepted_seconds: 10, warnings: ['identity_unverified', 'periodicity_is_not_voice_detection'] }
}

export function voiceProfile(id = '1234567890abcdef1234567890abcdef'): VoiceProfileResponse {
  return { id, name: 'My voice', created_at: '2026-10-01T10:00:00Z', recordings: [{ filename: 'song.wav', bytes: 100 }], status: 'idle', stage: '', detail: '', progress_current: 0, progress_total: 1, error: '', error_code: '', trained_steps: 0, built_from: [], has_preview: false, usable: false,
    models: [{ id: 'base', steps: 0, kind: 'base', resume_available: false }, { id: 'trained-200', steps: 200, kind: 'trained', resume_available: true }], active_model_id: 'base' }
}

export function comparison(): VoiceComparisonResponse {
  return { id: 'comparison-1', voice_id: voiceProfile().id, status: 'done', request: { source_id: 'abcdef1234567890abcdef1234567890', model_ids: ['base'], reference_ids: ['reference-1'], diffusion_steps: [30], seed: 42, start_sec: 0, duration_sec: 10, input_kind: 'vocal', separation_quality: 'fast' },
    source_filename: 'held-out.wav', error_code: '', trials: [{ id: 'trial-1', model_id: 'base', reference_id: 'reference-1', diffusion_steps: 30, status: 'done', error_code: '', audio_url: '/trial.wav',
      metrics: { duration_sec: 10.1, duration_delta_sec: 0.1, level_db: -18, peak: 0.8, clipped_fraction: 0 }, rating: null }] }
}
