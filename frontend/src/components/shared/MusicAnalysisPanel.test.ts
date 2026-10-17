// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import MusicAnalysisPanel from './MusicAnalysisPanel.vue'
import * as api from '../../api/musicAnalysis'
import type { AnalysisResult, MusicAnalysisJob, MusicAnalysisRequest } from '../../api/generated'
import { ApiError } from '../../api/http'
import { musicAnalysisEn } from '../../locales/musicAnalysis'
import { exportQuality } from '../../locales/exportQuality'
vi.mock('../../api/musicAnalysis', async original => ({ ...await original<typeof import('../../api/musicAnalysis')>(), previewMusicAnalysis: vi.fn(), startMusicAnalysis: vi.fn(), listMusicAnalysis: vi.fn(), getMusicAnalysis: vi.fn(), cancelMusicAnalysis: vi.fn(), retryMusicAnalysis: vi.fn(), saveMusicAnalysisOverrides: vi.fn() }))
const source: MusicAnalysisRequest['source'] = { kind: 'version', track_id: 42, version_id: 'a'.repeat(32) }
const timestamp = '2026-10-17T10:00:00Z'
const completed: AnalysisResult = { id: 'c'.repeat(32), source, audio_sha256: 'd'.repeat(64), sample_rate_hz: 48000, channels: 2, duration_frames: 480000, duration_sec: 10, analysis_sample_rate_hz: 16000, hop_sec: .1, analyzer_revision: 'test-v1', configuration_sha256: 'e'.repeat(64), loudness: { duration_sec: 10, integrated_lufs: -14, source_sha256: 'd'.repeat(64) }, created_at: timestamp, override_revision: 2, key: { status: 'unknown', unknown_reason: 'ambiguous', candidates: [{ tonic_pc: 0, mode: 'minor', score: .61 }] }, warnings: ['meter_unknown', 'vocal_activity_unavailable'] }
function job(overrides: Partial<MusicAnalysisJob> = {}): MusicAnalysisJob { return { id: 'b'.repeat(32), source, created_at: timestamp, status: 'running', phase: 'features', started_at: timestamp, ...overrides } }
let app: App | undefined
beforeEach(() => { vi.resetAllMocks(); vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-17T10:00:15Z')); vi.mocked(api.listMusicAnalysis).mockResolvedValue([]); vi.mocked(api.startMusicAnalysis).mockResolvedValue(job()); vi.mocked(api.getMusicAnalysis).mockResolvedValue(job()); vi.mocked(api.previewMusicAnalysis).mockResolvedValue({ source_bytes: 1000, retained_bytes: 1000, temporary_bytes: 2000, required_free_bytes: 3000, free_bytes: 4000, can_start: true }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 15; index++) await nextTick() }
async function mount() {
  const selectedSource = ref(source), node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(MusicAnalysisPanel, { source: selectedSource.value }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en: { musicAnalysis: musicAnalysisEn, exportQuality } } }))
  app.mount(node); await settle(); return { node, selectedSource }
}
function button(node: HTMLElement, label: string) { const found = [...node.querySelectorAll('button')].find(element => element.textContent?.trim() === label || element.getAttribute('aria-label') === label); if (!found) throw new Error(`Missing button: ${label}`); return found }
async function open(node: HTMLElement) { button(node, 'Music analysis').click(); await settle() }
async function enter(node: HTMLElement, label: string, value: string) { const details = node.querySelector('details'); if (details) details.open = true; const input = node.querySelector(`input[aria-label="${label}"]`); if (!(input instanceof HTMLInputElement)) throw new Error(`Missing input: ${label}`); input.value = value; input.dispatchEvent(new Event('input')); await settle() }

it('stays collapsed without HTTP and never starts work merely because the panel opens', async () => {
  const { node } = await mount(); expect(api.listMusicAnalysis).not.toHaveBeenCalled()
  expect(api.previewMusicAnalysis).not.toHaveBeenCalled()
  await open(node); expect(api.listMusicAnalysis).toHaveBeenCalledTimes(1); expect(api.startMusicAnalysis).not.toHaveBeenCalled()
  button(node, 'Start analysis').click(); await settle(); expect(api.startMusicAnalysis).toHaveBeenCalledWith({ source }, expect.any(AbortSignal))
})

it('shows retained-copy and scratch costs, then refreshes changed free space without resubmitting', async () => {
  vi.mocked(api.previewMusicAnalysis).mockResolvedValueOnce({ source_bytes: 1000, retained_bytes: 1000, temporary_bytes: 2000, required_free_bytes: 3000, free_bytes: 2000, can_start: false })
  const { node } = await mount(); await open(node)
  expect(node.textContent).toContain('Retained source copy'); expect(node.textContent).toContain('Temporary space')
  expect(button(node, 'Start analysis').disabled).toBe(true)
  button(node, 'Refresh analysis').click(); await settle()
  expect(button(node, 'Start analysis').disabled).toBe(false)
  expect(api.startMusicAnalysis).not.toHaveBeenCalled()
})

it('marks a failed refreshed forecast stale before allowing another start', async () => {
  const { node } = await mount(); await open(node)
  vi.mocked(api.previewMusicAnalysis).mockRejectedValueOnce(new Error('/private/disk'))
  button(node, 'Refresh analysis').click(); await settle()
  expect(button(node, 'Start analysis').disabled).toBe(true)
  expect(node.textContent).toContain('Storage estimate is unavailable or out of date')
  expect(node.textContent).not.toContain('/private/disk')
})

it('invalidates the displayed forecast when free space changes before the server admits work', async () => {
  vi.mocked(api.startMusicAnalysis).mockRejectedValue(new ApiError('insufficient_disk', 409))
  const { node } = await mount(); await open(node); button(node, 'Start analysis').click(); await settle()
  expect(node.textContent).toContain('not enough verified free disk space')
  expect(button(node, 'Start analysis').disabled).toBe(true)
  vi.mocked(api.previewMusicAnalysis).mockResolvedValueOnce({ source_bytes: 1000, retained_bytes: 1000, temporary_bytes: 2000, required_free_bytes: 3000, free_bytes: 4000, can_start: true })
  button(node, 'Refresh analysis').click(); await settle()
  expect(button(node, 'Start analysis').disabled).toBe(false)
  expect(api.startMusicAnalysis).toHaveBeenCalledTimes(1)
})

it('allows cancellation of a known job after storage and history refresh both fail', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValueOnce([job()]).mockRejectedValue(new Error('offline'))
  vi.mocked(api.cancelMusicAnalysis).mockResolvedValue(job({ status: 'cancelled' }))
  const { node } = await mount(); await open(node)
  vi.mocked(api.previewMusicAnalysis).mockRejectedValue(new Error('offline'))
  button(node, 'Refresh analysis').click(); await settle()
  expect(button(node, 'Cancel analysis').disabled).toBe(false)
  button(node, 'Cancel analysis').click(); await settle()
  expect(api.cancelMusicAnalysis).toHaveBeenCalledWith(job().id, expect.any(AbortSignal))
})

it('reattaches only the exact source and cancels its existing job without cancelling on teardown', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ source: { ...source, version_id: 'f'.repeat(32) } }), job()])
  vi.mocked(api.cancelMusicAnalysis).mockResolvedValue(job({ status: 'cancelled', phase: 'waiting' }))
  const { node } = await mount(); await open(node)
  expect(node.textContent).toContain('15 s elapsed'); expect(api.startMusicAnalysis).not.toHaveBeenCalled()
  button(node, 'Cancel analysis').click(); await settle(); expect(api.cancelMusicAnalysis).toHaveBeenCalledWith(job().id, expect.any(AbortSignal))
  app?.unmount(); app = undefined; expect(api.cancelMusicAnalysis).toHaveBeenCalledTimes(1)
})

it('renders estimates and unknowns explicitly and reuses measured loudness', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ status: 'done', phase: 'complete', result: completed })])
  const { node } = await mount(); await open(node)
  expect(node.textContent).toContain('Tempo: Unknown'); expect(node.textContent).toContain('Beats per bar: Unknown')
  expect(node.textContent).toContain('Beat grid: Unknown'); expect(node.textContent).toContain('Vocal activity: Unknown')
  expect(node.textContent).toContain('Key estimate: Unknown'); expect(node.textContent).toContain('C minor')
  expect(node.querySelector('[data-audio-quality]')?.textContent).toContain('-14.0 LUFS')
  expect(node.textContent).not.toContain('4/4')
})

it('shows safe offline errors and reconnects without creating another job', async () => {
  vi.mocked(api.listMusicAnalysis).mockRejectedValueOnce(new Error('/private/model/token'))
  const { node } = await mount(); await open(node)
  expect(node.textContent).toContain('Could not load analysis'); expect(node.textContent).not.toContain('/private')
  button(node, 'Refresh analysis').click(); await settle(); expect(api.listMusicAnalysis).toHaveBeenCalledTimes(2); expect(api.startMusicAnalysis).not.toHaveBeenCalled()
})

it('ignores a late list response after the selected source changes and aborts old reads', async () => {
  let finish: ((value: MusicAnalysisJob[]) => void) | undefined
  vi.mocked(api.listMusicAnalysis).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { node, selectedSource } = await mount(); await open(node)
  const signal = vi.mocked(api.listMusicAnalysis).mock.calls[0]?.[1]
  selectedSource.value = { ...source, version_id: 'f'.repeat(32) }; await settle(); expect(signal?.aborted).toBe(true)
  finish?.([job({ status: 'done', phase: 'complete', result: completed })]); await settle()
  expect(node.querySelector('[data-audio-quality]')).toBeNull(); expect(button(node, 'Start analysis').disabled).toBe(false)
})

it('reloads an older source correction even when global history contains one hundred newer unrelated jobs', async () => {
  const old = job({ status: 'done', phase: 'complete', result: { ...completed, tempo: { status: 'known', bpm: 87, confidence_method: 'manual' } } })
  const unrelated = Array.from({ length: 100 }, () => job({ source: { ...source, version_id: 'f'.repeat(32) } }))
  vi.mocked(api.listMusicAnalysis).mockImplementation(async requested => requested?.kind === 'version' && requested.version_id === source.version_id ? [old] : unrelated)
  const { node } = await mount(); await open(node)
  expect(api.listMusicAnalysis).toHaveBeenCalledWith(source, expect.any(AbortSignal))
  expect(node.textContent).toContain('Tempo: 87 BPM')
  button(node, 'Music analysis').click(); await settle(); await open(node)
  expect(node.textContent).toContain('Tempo: 87 BPM')
  expect(api.startMusicAnalysis).not.toHaveBeenCalled()
})

it('saves only explicit manual values using the result identity and current CAS revision', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ status: 'done', phase: 'complete', result: completed })])
  vi.mocked(api.saveMusicAnalysisOverrides).mockResolvedValue({ ...completed, override_revision: 3, tempo: { status: 'known', bpm: 96, confidence_method: 'manual' } })
  const { node } = await mount(); await open(node); await enter(node, 'Manual tempo (BPM)', '96')
  expect(button(node, 'Save corrections').disabled).toBe(false)
  expect(node.querySelector('form')?.checkValidity()).toBe(true)
  button(node, 'Save corrections').click(); await settle()
  expect(api.saveMusicAnalysisOverrides).toHaveBeenCalledWith({ analysis_id: completed.id, expected_revision: 2, tempo: { status: 'known', bpm: 96, confidence: null, confidence_method: 'manual', half_double_ambiguous: false, map: [] } }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Tempo: 96 BPM'); expect(node.textContent).toContain('Manual correction')
})

it('refreshes revision conflicts while preserving the correction draft for explicit review', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ status: 'done', phase: 'complete', result: completed })])
  vi.mocked(api.saveMusicAnalysisOverrides).mockRejectedValueOnce(new ApiError('analysis_revision_conflict', 409)).mockResolvedValue({ ...completed, override_revision: 4 })
  vi.mocked(api.getMusicAnalysis).mockResolvedValue(job({ status: 'done', phase: 'complete', result: { ...completed, override_revision: 3 } }))
  const { node } = await mount(); await open(node); await enter(node, 'Manual tempo (BPM)', '96'); button(node, 'Save corrections').click(); await settle()
  expect(node.textContent).toContain('Corrections changed elsewhere')
  expect(node.querySelector<HTMLInputElement>('[aria-label="Manual tempo (BPM)"]')?.value).toBe('96')
  button(node, 'Save corrections').click(); await settle()
  expect(api.saveMusicAnalysisOverrides).toHaveBeenLastCalledWith(expect.objectContaining({ expected_revision: 3 }), expect.any(AbortSignal))
})

it('aborts a pending correction after a source change and never replaces the new selection', async () => {
  let finish: ((value: AnalysisResult) => void) | undefined
  vi.mocked(api.listMusicAnalysis).mockResolvedValueOnce([job({ status: 'done', phase: 'complete', result: completed })]).mockResolvedValue([])
  vi.mocked(api.saveMusicAnalysisOverrides).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node, selectedSource } = await mount(); await open(node); await enter(node, 'Manual tempo (BPM)', '96'); button(node, 'Save corrections').click(); await settle()
  const signal = vi.mocked(api.saveMusicAnalysisOverrides).mock.calls[0]?.[1]
  selectedSource.value = { ...source, version_id: 'f'.repeat(32) }; await settle(); expect(signal?.aborted).toBe(true)
  finish?.({ ...completed, override_revision: 3 }); await settle(); expect(node.querySelector('[data-audio-quality]')).toBeNull()
})

it('saves typed manual key and beats-per-bar corrections without inventing a time-signature denominator', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ status: 'done', phase: 'complete', result: completed })])
  vi.mocked(api.saveMusicAnalysisOverrides).mockResolvedValue({ ...completed, override_revision: 3 })
  const { node } = await mount(); await open(node); await enter(node, 'Manual beats per bar', '7')
  const select = node.querySelector('[name="analysis-manual-key"]'); if (!(select instanceof HTMLSelectElement)) throw new Error('Missing manual key')
  select.value = '9:minor'; select.dispatchEvent(new Event('change')); await settle(); button(node, 'Save corrections').click(); await settle()
  expect(api.saveMusicAnalysisOverrides).toHaveBeenCalledWith({ analysis_id: completed.id, expected_revision: 2,
    meter: { status: 'known', beats_per_bar: 7, method: 'manual', confidence: null },
    key: { status: 'known', tonic_pc: 9, mode: 'minor', confidence: null, method: 'manual', candidates: [], unknown_reason: null } }, expect.any(AbortSignal))
})

it('rejects out-of-range manual values without sending corrections', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ status: 'done', phase: 'complete', result: completed })])
  const { node } = await mount(); await open(node); await enter(node, 'Manual tempo (BPM)', '401')
  expect(button(node, 'Save corrections').disabled).toBe(true)
  await enter(node, 'Manual tempo (BPM)', '96.05'); await enter(node, 'Manual beats per bar', '3.5')
  expect(button(node, 'Save corrections').disabled).toBe(true); expect(api.saveMusicAnalysisOverrides).not.toHaveBeenCalled()
})

it('aborts polling after teardown and never cancels backend work when a late poll completes', async () => {
  let finish: ((value: MusicAnalysisJob) => void) | undefined
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ progress: { current: 2, total: 8, unit: 'windows' } })])
  vi.mocked(api.getMusicAnalysis).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node } = await mount(); await open(node)
  expect(node.textContent).toContain('2 / 8 windows'); expect(node.textContent).not.toContain('25%')
  await vi.advanceTimersByTimeAsync(2000); await settle()
  const signal = vi.mocked(api.getMusicAnalysis).mock.calls[0]?.[1]
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  finish?.(job()); await settle(); await vi.advanceTimersByTimeAsync(10000)
  expect(api.getMusicAnalysis).toHaveBeenCalledTimes(1); expect(api.cancelMusicAnalysis).not.toHaveBeenCalled()
})

it('reviews the explicitly selected beat profile without starting or silently falling back', async () => {
  const { node } = await mount(); await open(node)
  const method = node.querySelector<HTMLSelectElement>('[name="analysis-profile"]'); if (!method) throw new Error('Missing analysis method')
  method.value = 'music-beats-v1'; method.dispatchEvent(new Event('change')); await settle()
  expect(api.previewMusicAnalysis).toHaveBeenLastCalledWith({ source, profile: 'music-beats-v1' }, expect.any(AbortSignal))
  expect(api.startMusicAnalysis).not.toHaveBeenCalled()
  button(node, 'Start analysis').click(); await settle()
  expect(api.startMusicAnalysis).toHaveBeenCalledExactlyOnceWith({ source, profile: 'music-beats-v1' }, expect.any(AbortSignal))
})
it('blocks the beat method when its explicit local checkpoint is unavailable', async () => {
  const { node } = await mount(); await open(node)
  vi.mocked(api.previewMusicAnalysis).mockRejectedValueOnce(new ApiError('analysis_weights_missing', 409))
  const method = node.querySelector<HTMLSelectElement>('[name="analysis-profile"]'); if (!method) throw new Error('Missing analysis method')
  method.value = 'music-beats-v1'; method.dispatchEvent(new Event('change')); await settle()
  expect(button(node, 'Start analysis').disabled).toBe(true)
  expect(api.startMusicAnalysis).not.toHaveBeenCalled()
  expect(node.querySelector('a[href="/settings#module-music_beats"]')).not.toBeNull()
})

it('explains the saved detector capability rather than the method selected for the next job', async () => {
  vi.mocked(api.listMusicAnalysis).mockResolvedValue([job({ status: 'done', phase: 'complete', result: { ...completed, engines: [{ task: 'beats', name: 'beat-this-small0', revision: 'beat-this-small0-cpu-v1', configuration_sha256: 'e'.repeat(64), weights_sha256: 'f'.repeat(64) }] } })])
  const { node } = await mount(); await open(node)
  expect(node.textContent).toContain('Estimated beat and downbeat positions need listening review')
  expect(node.textContent).not.toContain('does not detect tempo, meter, beats')
  expect(node.querySelector<HTMLSelectElement>('[name="analysis-profile"]')?.value).toBe('music-v1')
})
