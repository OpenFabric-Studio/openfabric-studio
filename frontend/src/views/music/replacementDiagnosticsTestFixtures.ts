import { parseReplacementDiagnostics } from '../../api/generated'

export function replacementDiagnosticsFixture() {
  const identity = (id: string) => ({ artifact_id: id.repeat(32), sha256: id.repeat(64), sample_rate_hz: 8000, channels: 1, duration_frames: 120000 })
  const windows = [0, 1, 2].map(n => ({ start_frame: n * 40000, end_frame: (n + 1) * 40000 }))
  const observation = (id: string, role: string, hz: number) => ({ identity: identity(id), role,
    applied_pitch_semitones: 0, status: 'observed', sampled_sec: 15, analyzed_sec: 14.64, voiced_sec: 14.64,
    reliable_voiced_sec: 14.64, pitch_p05_hz: hz, pitch_median_hz: hz, pitch_p95_hz: hz,
    windows: windows.map(window => ({ window, analyzed_sec: 4.88, voiced_sec: 4.88, reliable_voiced_sec: 4.88,
      pitch_p05_hz: hz, pitch_median_hz: hz, pitch_p95_hz: hz, median_voicing_probability: .95 })) })
  return parseReplacementDiagnostics({ revision: 1, request_sha256: 'f'.repeat(64), attempt_id: 'e'.repeat(32),
    created_at: '2026-10-17T10:00:00Z', pitch: { configuration_sha256: 'f'.repeat(64), runtime: { python: '3.12', librosa: '0.11' },
      artifacts: [observation('a', 'reference', 220), observation('b', 'candidate', 196)],
      comparisons: [{ kind: 'prepared_fit', reference_artifact_id: 'a'.repeat(32), candidate_artifact_id: 'b'.repeat(32),
        status: 'observed', paired_windows: 3, supported_windows: 3, median_delta_semitones: 2, spread_semitones: 0,
        recommendation: { pitch_shift_semitones: 2, pitch_nudge_semitones: 0 } }] },
    tonal: { plan_id: 'c'.repeat(32), revision: 1, configuration_sha256: 'f'.repeat(64), ffmpeg_version: 'fixture', numpy_version: 'fixture',
      artifacts: [['a', 'source_vocal'], ['d', 'backing'], ['b', 'new_vocal']].map(([id, role]) => {
        if (!id) throw new Error('Missing fixture identity')
        return { identity: identity(id), role, windows, active_samples: 330750 }
      }), matching_eq: { status: 'available', before_difference_db: 2, after_difference_db: 1,
        bands: [125, 250, 500, 1000, 2000, 4000].map(frequency_hz => ({ frequency_hz, status: 'supported', reference_db: -14, new_db: -16, difference_db: 2, suggested_gain_db: 1 })),
        suggested_eq_bands: [{ frequency_hz: 125, gain_db: 1 }] },
      bleed: [{ vocal_artifact_id: 'a'.repeat(32), backing_artifact_id: 'd'.repeat(32), status: 'measured',
        correlation_strength: 'high', median_absolute_correlation: .8, windows: [{ window_index: 0, active_samples: 22050,
          vocal_channel: 0, backing_channel: 0, signed_correlation: -.8, absolute_correlation: .8,
          lag_frames: 2205, lag_ms: 100, polarity: 'inverted', lag_ambiguous: true }] }] } })
}
