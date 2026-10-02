import type { YueJobResponse } from '../api/contracts'
import { completeYueOptions } from '../api/yueJobs'
export function yueJobResponse(overrides: Partial<YueJobResponse> = {}): YueJobResponse {
  return { native_progress_available: false, id: 'a'.repeat(32), status: 'queued', stage: 'queued', queue_reason: '',
    created_at: '2026-10-01T10:00:00Z', started_at: null, elapsed_seconds: 0, phase_eta_seconds: null,
    progress: null, title: 'Jazz', lyrics: 'lyrics', seed: 1, precision: 'q8_0',
    options: completeYueOptions({ style: 'jazz', cot: 'off' }), voice_id: null, error_code: '', track: null, ...overrides }
}
