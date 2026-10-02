import type { SavedTrack, VideoProject, VideoReadinessResponse } from '../../api/contracts'

export const videoReadinessFixture: VideoReadinessResponse = { engine_ready: true, analysis_ready: true, ffmpeg_ready: true, overlay_ready: true, modes: ['generated', 'cover', 'visualizer'], warnings: [], options: [{ id: 'ltx23', name: 'LTX-2.3', available: true, reason: '', total_bytes: 58_000_000_000, uncached_bytes: 0, free_bytes: 100_000_000_000, model_revision: 'fixture', text_revision: 'fixture', warnings: [] }, { id: 'ltx25', name: 'LTX-2.5', available: false, reason: 'model_missing', total_bytes: 64_000_000_000, uncached_bytes: 64_000_000_000, free_bytes: 100_000_000_000, model_revision: 'fixture', text_revision: '', warnings: [] }] }

export const videoTrack: SavedTrack = { id: 1, short_id: 1, model: 'upload', created_at: '2026-10-01T10:00:00Z', title: 'Test song', lyrics: '', seed: null, duration_ms: 30_000, wall_ms: null, params: {}, filename: 'song.wav', audio_url: '/api/tracks/1/audio', abc_url: null, stems: null, midi: null }

export function videoProjectFixture(id = 'a'.repeat(32), trackId = 1): VideoProject {
  return { id, revision: 1, track_id: trackId, track_title: 'Test song', name: 'Test video', mode: 'generated', direction: '', seed: 17, duration_sec: 30, source_fingerprint: 'fixture', source_changed: false,
    created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-01T10:00:00Z', settings: { engine_pack: 'ltx23', width: 704, height: 448, stage1_steps: 30, stage2_steps: 3, cfg_scale: 3, negative_prompt: '' }, export_settings: { aspect: 'landscape', quality: 'standard', include_overlays: true },
    shots: [{ id: 'b'.repeat(32), start_sec: 0, seconds: 8, prompt: 'Reviewed first scene', seed: 17, variants: [], approved_variant_id: null }, { id: 'c'.repeat(32), start_sec: 8, seconds: 4, prompt: 'Reviewed second scene', seed: 18, variants: [], approved_variant_id: null }], references: [], overlays: [], markers: [], analysis: null, job: null, file_url: '', poster_url: '', warnings: [] }
}
