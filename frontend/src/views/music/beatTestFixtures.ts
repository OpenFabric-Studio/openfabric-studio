import type { BeatDocument, BeatJob, BeatJobRequest, BeatKitInfo } from '../../api/generated'

export function beatDocument(): BeatDocument {
  return { id: 'a'.repeat(32), revision: 0, title: 'Test rhythm', bpm: 120, seed: 4471, kit_id: 'test-kit', kit_manifest_sha256: 'b'.repeat(64), global_swing: .5,
    patterns: [{ id: 'A', bars: 1, meter: { numerator: 4, denominator: 4 }, steps_per_quarter: 4,
      lanes: Array.from({ length: 8 }, (_, index) => ({ lane_id: `lane-${index}`, label: `Drum ${index + 1}`, instrument_id: 'test-drum', role: 'drum', muted: false, gain_db: -6, pan: 0, swing: null, steps: [] })) }], chain: [{ pattern_id: 'A', repeats: 1, section_label: '' }] }
}
export function beatKit(): BeatKitInfo {
  return { sha256: 'b'.repeat(64), manifest: { kit_id: 'test-kit', revision: 'test-v1', instruments: [{ instrument_id: 'test-drum', label: 'Test drum', role: 'drum', regions: [{ region_id: 'test-region', asset_id: 'test-asset', sha256: 'c'.repeat(64), byte_size: 192044, sample_rate_hz: 48000, channels: 1, frames: 48000, root_note_midi: 36, attribution: 'Synthetic test fixture', terms: { component_id: 'test-sample', name: 'Synthetic test sample', scope: 'samples', status: 'unknown', notes: 'No recording rights claim', reviewed_at: '2026-10-17' } }] }] } }
}
export function beatRequest(): BeatJobRequest { return { beat_id: 'a'.repeat(32), expected_revision: 0, mode: 'full', submission_key: 'd'.repeat(32) } }
export function beatJob(changes: Partial<BeatJob> = {}): BeatJob {
  return { id: 'e'.repeat(32), beat_id: 'a'.repeat(32), beat_revision: 0, request: beatRequest(), status: 'running', phase: 'rendering', created_at: '2026-10-17T12:00:00Z', started_at: '2026-10-17T12:00:00Z', progress_frames: 48000, total_frames: 96000, ...changes }
}
