// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import TrackAudioVersions from './TrackAudioVersions.vue'
import { i18n, setLocale } from '../../i18n'
import type { AudioVersion, AudioExportResponse, VoiceJobProgress } from '../../api/contracts'
import * as downloadApi from '../../api/trackDownload'
vi.mock('../../api/trackDownload', () => ({ downloadTrackAudio: vi.fn() }))
const api = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), cancel: vi.fn(), retry: vi.fn(), exports: vi.fn(), export: vi.fn(), cancelExport: vi.fn(), retryExport: vi.fn(), voices: vi.fn() }))
vi.mock('../../api/audioVersions', () => ({ listAudioVersions: api.list, createAudioVersion: api.create, cancelAudioVersion: api.cancel, retryAudioVersion: api.retry }))
vi.mock('../../api/audioExports', () => ({ listAudioExports: api.exports, createAudioExport: api.export, cancelAudioExport: api.cancelExport, retryAudioExport: api.retryExport }))
vi.mock('../../api/voices', () => ({ listVoices: api.voices }))
vi.mock('../../composables/audioPlayback', async original => ({ ...await original<typeof import('../../composables/audioPlayback')>(), fetchAndComputePeaks: vi.fn().mockResolvedValue([.2, .7]) }))
const original: AudioVersion = { id: 'a'.repeat(32), track_id: 42, kind: 'original', status: 'done', created_at: '2026-10-01T10:00:00Z', audio_url: '/original.wav', filename: 'original.wav' }
const voice: AudioVersion = { ...original, id: 'b'.repeat(32), kind: 'voice', voice_name: 'Singer', voice_id: 'c'.repeat(32), audio_url: '/voice.wav', source_version_id: original.id }
const queued: AudioVersion = { ...voice, id: 'd'.repeat(32), status: 'queued', audio_url: null, voice_name: 'Other singer' }
const exported: AudioExportResponse = { id: 'e'.repeat(32), track_id: 42, version_id: original.id, format: 'mp3', status: 'done', created_at: 'now', audio_url: '/original.mp3', filename: 'original.mp3', settings: { mp3: { bitrate_kbps: 320, mode: 'cbr', sample_rate: 48000, channels: 2 } } }
let app: App | undefined
let currentTrack = ref(42)
const playedSources: string[] = []
beforeEach(() => {
  vi.clearAllMocks(); setLocale('en'); playedSources.length = 0
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) { this.dispatchEvent(new Event('pause')) })
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) { playedSources.push(this.getAttribute('src') ?? ''); this.dispatchEvent(new Event('play')); return Promise.resolve() })
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, voice] }); api.voices.mockResolvedValue([{ id: 'c'.repeat(32), name: 'Other singer', usable: true }]); api.exports.mockResolvedValue({ exports: [] }); api.create.mockResolvedValue(queued); api.export.mockResolvedValue(exported)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
function mount(fallbackAudioUrl?: string, fallbackFilename?: string, onPlaying?: (value: boolean) => void) { currentTrack = ref(42); const node = document.body.appendChild(document.createElement('div')); app = createApp({ render: () => h(TrackAudioVersions, { trackId: currentTrack.value, fallbackAudioUrl, fallbackFilename, onPlaying }) }).use(i18n); app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' }); app.mount(node); return node }
async function settle() { for (let i=0;i<12;i++) await nextTick() }
function button(node: HTMLElement, text: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === text); if (!found) throw new Error(`Missing ${text}`); return found }
function formatButton(node: HTMLElement, prefix: string) {
  const found = [...node.querySelectorAll<HTMLButtonElement>('[role="group"][aria-label="Playback format"] button')].find(item => item.textContent?.trim().startsWith(prefix))
  if (!found) throw new Error(`Missing playback format ${prefix}`)
  return found
}
function exportsForOriginal(items: AudioExportResponse[] = [exported]) { api.exports.mockImplementation((_trackId: number, versionId: string) => Promise.resolve({ exports: versionId === original.id ? items : [] })) }
it('forwards playing and processing state for cards retained by pagination', async () => {
  const playing: boolean[] = [], processing: boolean[] = []
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, queued] })
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TrackAudioVersions, { trackId: 42, onPlaying: (value: boolean) => playing.push(value), onProcessing: (value: boolean) => processing.push(value) }) }).use(i18n)
  app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' }); app.mount(node); await settle()
  expect(processing.at(-1)).toBe(true)
  node.querySelector('audio')?.dispatchEvent(new Event('play')); await settle(); expect(playing.at(-1)).toBe(true)
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, voice] })
  button(node, 'Refresh').click(); await settle(); expect(processing.at(-1)).toBe(false)
  app.unmount(); app = undefined; expect(playing.at(-1)).toBe(false)
})
it('switches between immutable original and voice playback and downloads', async () => { const node = mount(); await settle(); expect(node.querySelector('audio')?.src).toContain('/voice.wav'); button(node, 'Original').click(); await settle(); expect(node.querySelector('audio')?.src).toContain('/original.wav'); expect(node.querySelector('a[download]')?.getAttribute('href')).toBe('/original.wav'); expect(api.exports).toHaveBeenLastCalledWith(42, original.id, expect.any(AbortSignal)) })

it('does not display another inspected version analysis over the retained native player', async () => {
  vi.useFakeTimers()
  api.list.mockResolvedValue({track_id:42,original_available:true,versions:[original,queued]})
  api.exports.mockImplementation((_trackId: number,versionId: string) => Promise.resolve({exports:versionId===queued.id ? [{...exported,id:'e'.repeat(32),version_id:queued.id,operation:'analyze',audio_url:null,input_metrics:{duration_sec:1,integrated_lufs:-3,true_peak_dbtp:2,sample_peak_dbfs:0}}] : []}))
  const node=mount();await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav')
  button(node,'Other singer · Queued').click();await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav')
  expect(node.querySelector('[data-audio-quality]')).toBeNull()
  expect(node.textContent).not.toContain('-3.0 LUFS')
})

it('does not substitute dry-source measurements for an unmeasured processed export', async () => {
  const sourceAnalysis: AudioExportResponse = { ...exported, id: 'd'.repeat(32), operation: 'analyze', audio_url: null, input_metrics: { duration_sec: 1, integrated_lufs: -7, true_peak_dbtp: 3, sample_peak_dbfs: -1 } }
  exportsForOriginal([{ ...exported, output_metrics: null, target_result: 'inconclusive' }, sourceAnalysis])
  const node = mount(); await settle(); button(node, 'Original').click(); await settle()
  expect(node.querySelector('[data-audio-quality]')?.textContent).toContain('-7.0 LUFS')
  formatButton(node, 'MP3').click(); await settle()
  expect(node.querySelector('[data-audio-quality]')?.textContent ?? '').not.toContain('-7.0 LUFS')
  expect(node.textContent).toContain('Measurements are unavailable for this selected file.')
  expect(node.textContent).toContain('Target could not be verified')
})

it('runs persisted source diagnostics without replacing selected playback', async () => {
  const node = mount(); await settle()
  button(node, 'Analyze source').click(); await settle()
  expect(api.export).toHaveBeenCalledWith(42, voice.id, 'wav', expect.any(AbortSignal), { operation: 'analyze' })
  expect(node.querySelector('audio')?.src).toContain('/voice.wav')
})
it('tags the exact selected export without changing playback or raw download selection', async () => {
  exportsForOriginal(); vi.mocked(downloadApi.downloadTrackAudio).mockResolvedValue({ blob: new Blob(['audio']), filename: 'Singer - Song.mp3' })
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:download'); vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {}); vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const node = mount(); await settle(); button(node, 'Original').click(); await settle(); formatButton(node, 'MP3').click(); await settle()
  button(node, 'Download with metadata').click(); await settle()
  expect(downloadApi.downloadTrackAudio).toHaveBeenCalledWith(42, original.id, exported.id, undefined, expect.any(AbortSignal))
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.mp3'); expect(node.querySelector('a[download]')?.getAttribute('href')).toBe('/original.mp3')
  app?.unmount(); app = undefined; expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:download')
})
it('creates another voice without altering the existing versions', async () => { const node = mount(); await settle(); button(node, 'Add voice version').click(); await settle(); expect(api.create).toHaveBeenCalledWith(42, 'c'.repeat(32), expect.any(AbortSignal)); expect(node.textContent).toContain('Other singer'); button(node, 'Original').click(); await settle(); expect(node.querySelector('audio')?.src).toContain('/original.wav') })
it('exports the selected version and shows the captured quality', async () => { const node = mount(); await settle(); button(node, 'Original').click(); await settle(); button(node, 'Create export').click(); await settle(); expect(api.export).toHaveBeenCalledWith(42, original.id, 'mp3', expect.any(AbortSignal)); expect(node.textContent).toContain('320 kbps'); expect(node.querySelector('a[href="/original.mp3"]')?.hasAttribute('download')).toBe(true) })
it('does not offer another conversion when the original is unavailable', async () => { api.list.mockResolvedValue({ track_id:42, original_available:false, versions:[voice] }); const node=mount(); await settle(); expect(button(node, 'Add voice version').disabled).toBe(true); expect(node.textContent).toContain('original audio is unavailable') })
it('discards an action that completes after unmount and never restarts polling', async () => { vi.useFakeTimers(); let finish: ((value: AudioVersion) => void) | undefined; api.create.mockReturnValue(new Promise<AudioVersion>(resolve => { finish=resolve })); const node=mount(); await settle(); button(node,'Add voice version').click(); await settle(); app?.unmount(); app=undefined; const before=api.list.mock.calls.length; finish?.(queued); await settle(); await vi.advanceTimersByTimeAsync(9000); expect(api.list).toHaveBeenCalledTimes(before); expect(vi.getTimerCount()).toBe(0) })
it('reports unavailable voice inventory without claiming no voices exist', async () => { api.voices.mockRejectedValue(new Error('/private/engine')); const node=mount(); await settle(); expect(node.textContent).toContain('Could not load voices'); expect(node.textContent).not.toContain('/private/engine'); expect(button(node,'Add voice version').disabled).toBe(true) })
it('keeps a user-selected original while conversion status refreshes', async () => { vi.useFakeTimers(); api.list.mockResolvedValueOnce({ track_id:42, original_available:true, versions:[original, { ...voice, status:'running', audio_url:null }] }).mockResolvedValue({ track_id:42, original_available:true, versions:[original,voice] }); const node=mount(); await settle(); expect(node.querySelector('audio')?.src).toContain('/original.wav'); await vi.advanceTimersByTimeAsync(2500); await settle(); expect(node.querySelector('audio')?.src).toContain('/original.wav'); expect(button(node,'Original').getAttribute('aria-pressed')).toBe('true') })
it('translates queued versions and exposes owned cancel/retry actions', async () => { api.list.mockResolvedValue({ track_id:42,original_available:true,versions:[original,queued] }); api.cancel.mockResolvedValue({ ...queued,status:'cancelled',error_code:'cancelled' }); api.retry.mockResolvedValue(queued); const node=mount(); await settle(); button(node,'Other singer · Queued').click(); await settle(); button(node,'Cancel').click(); await settle(); expect(api.cancel).toHaveBeenCalledWith(42,queued.id,expect.any(AbortSignal)); button(node,'Retry').click(); await settle(); expect(api.retry).toHaveBeenCalledWith(42,queued.id,expect.any(AbortSignal)); expect(node.textContent).not.toContain('jobStatus.') })

it('keeps initial loading silent and clicking a completed version starts the main player', async () => {
  const node = mount(); await settle()
  expect(playedSources).toEqual([])
  button(node, 'Original').click(); await settle()
  expect(playedSources).toEqual(['/original.wav'])
  expect(node.querySelectorAll('audio')).toHaveLength(1)
})

it('plays the exact completed export and downloads the selected file with its format label', async () => {
  exportsForOriginal()
  const node = mount(); await settle(); button(node, 'Original').click(); await settle()
  formatButton(node, 'MP3').click(); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.mp3')
  expect(playedSources).toEqual(['/original.wav', '/original.mp3'])
  expect(formatButton(node, 'MP3').getAttribute('aria-pressed')).toBe('true')
  expect(formatButton(node, 'Source · WAV').getAttribute('aria-pressed')).toBe('false')
  expect(node.querySelector('a[download]')?.getAttribute('download')).toBe('original.mp3')
  expect(node.querySelector('a[download]')?.textContent).toContain('MP3')
  expect(node.querySelectorAll('audio')).toHaveLength(1)
})

it('does not toggle off an already playing file and can return to the native source', async () => {
  exportsForOriginal()
  const node = mount(); await settle(); button(node, 'Original').click(); await settle()
  formatButton(node, 'MP3').click(); await settle()
  vi.mocked(HTMLMediaElement.prototype.pause).mockClear()
  formatButton(node, 'MP3').click(); await settle()
  expect(playedSources).toEqual(['/original.wav', '/original.mp3'])
  expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled()
  formatButton(node, 'Source · WAV').click(); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav')
  expect(playedSources.at(-1)).toBe('/original.wav')
  expect(formatButton(node, 'Source · WAV').getAttribute('aria-pressed')).toBe('true')
})

it('distinguishes separate completed files in the same format and excludes unfinished exports', async () => {
  const alternate = { ...exported, id: 'f'.repeat(32), audio_url: '/original-vbr.mp3', filename: 'original-vbr.mp3', settings: { mp3: { mode: 'vbr', vbr_quality: 2 } } } satisfies AudioExportResponse
  exportsForOriginal([exported, alternate, { ...exported, id: '1'.repeat(32), format: 'flac', status: 'running', audio_url: null }, { ...exported, id: '2'.repeat(32), format: 'wav', status: 'failed', audio_url: null }])
  const node = mount(); await settle(); button(node, 'Original').click(); await settle()
  const choices = node.querySelectorAll('[role="group"][aria-label="Playback format"] button')
  expect(choices).toHaveLength(3)
  formatButton(node, 'MP3 · VBR q2').click(); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original-vbr.mp3')
  expect(node.querySelector('a[download]')?.getAttribute('download')).toBe('original-vbr.mp3')
})

it('preserves the selected export and avoids another play request through polling', async () => {
  vi.useFakeTimers()
  exportsForOriginal([exported, { ...exported, id: 'f'.repeat(32), status: 'running', audio_url: null }])
  const node = mount(); await settle(); button(node, 'Original').click(); await settle(); formatButton(node, 'MP3').click(); await settle()
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.mp3')
  expect(formatButton(node, 'MP3').getAttribute('aria-pressed')).toBe('true')
  expect(playedSources).toEqual(['/original.wav', '/original.mp3'])
  button(node, 'Singer').click(); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/voice.wav')
  expect(formatButton(node, 'Source · WAV').getAttribute('aria-pressed')).toBe('true')
})

it('keeps the chosen main audio while inspecting a queued version or creating another voice', async () => {
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, queued] })
  exportsForOriginal()
  const node = mount(); await settle(); formatButton(node, 'MP3').click(); await settle()
  const audio = node.querySelector('audio')
  button(node, 'Other singer · Queued').click(); await settle()
  expect(node.querySelector('audio')).toBe(audio)
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.mp3')
  button(node, 'Add voice version').click(); await settle()
  expect(node.querySelector('audio')).toBe(audio)
  expect(playedSources).toEqual(['/original.mp3'])
})

it('uses the fallback filename while the version request is loading without auto-playing', async () => {
  api.list.mockReturnValue(new Promise<never>(() => {}))
  const node = mount('/fallback.ogg?cache=1', 'Song.ogg'); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/fallback.ogg?cache=1')
  expect(node.querySelector('a[download]')?.getAttribute('download')).toBe('Song.ogg')
  expect(node.querySelector('a[download]')?.textContent).toContain('OGG')
  expect(playedSources).toEqual([])
})

it('discards export loading from a version that has already been switched away from', async () => {
  let finish: ((value: { exports: AudioExportResponse[] }) => void) | undefined
  api.exports.mockImplementation((_trackId: number, versionId: string) => versionId === original.id ? new Promise<{ exports: AudioExportResponse[] }>(resolve => { finish = resolve }) : Promise.resolve({ exports: [] }))
  const node = mount(); await settle(); button(node, 'Original').click(); await settle(); button(node, 'Singer').click(); await settle()
  finish?.({ exports: [exported] }); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/voice.wav')
  expect(node.querySelector('a[href="/original.mp3"]')).toBeNull()
  expect(playedSources).toEqual(['/original.wav', '/voice.wav'])
})

it('does not start requested playback after unmount', async () => {
  const node = mount(); await settle()
  button(node, 'Original').click(); app?.unmount(); app = undefined; await settle()
  expect(playedSources).toEqual([])
})

it('resets playback selection for a different track and ignores its previous late response', async () => {
  exportsForOriginal()
  const node = mount(); await settle(); button(node, 'Original').click(); await settle(); formatButton(node, 'MP3').click(); await settle()
  let finish: ((value: { track_id: number; original_available: boolean; versions: AudioVersion[] }) => void) | undefined
  api.list.mockReturnValueOnce(new Promise<{ track_id: number; original_available: boolean; versions: AudioVersion[] }>(resolve => { finish = resolve }))
  button(node, 'Refresh').click(); await settle()
  const nextVersion = { ...original, id: '3'.repeat(32), track_id: 43, audio_url: '/next.wav', filename: 'next.wav' }
  api.list.mockResolvedValue({ track_id: 43, original_available: true, versions: [nextVersion] })
  currentTrack.value = 43; await settle()
  finish?.({ track_id: 42, original_available: true, versions: [voice] }); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/next.wav')
  expect(playedSources).toEqual(['/original.wav', '/original.mp3'])
  expect(node.querySelector('a[download]')?.getAttribute('download')).toBe('next.wav')
})

it('only starts the latest file when choices arrive in the same render cycle', async () => {
  exportsForOriginal()
  const node = mount(); await settle(); button(node, 'Original').click(); await settle()
  playedSources.length = 0
  formatButton(node, 'MP3').click(); button(node, 'Singer').click(); await settle()
  expect(playedSources).toEqual(['/voice.wav'])
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/voice.wav')
})

it('retains playback ownership throughout an explicit file transition and releases it on pause', async () => {
  const playing: boolean[] = []
  exportsForOriginal()
  const node = mount(undefined, undefined, value => playing.push(value)); await settle()
  button(node, 'Original').click(); await settle(); playing.length = 0
  formatButton(node, 'MP3').click(); await settle()
  expect(playing).not.toContain(false)
  node.querySelector<HTMLButtonElement>('button[aria-label="Pause"]')?.click(); await settle()
  expect(playing.at(-1)).toBe(false)
})

it('does not let an older play completion release a newer pending source request', async () => {
  const playing: boolean[] = []
  let finishOld: (() => void) | undefined, rejectNew: ((cause: unknown) => void) | undefined
  vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(function (this: HTMLMediaElement) {
    this.dispatchEvent(new Event('play'))
    return new Promise<void>(resolve => { finishOld = resolve })
  }).mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { rejectNew = reject }))
  const node = mount(undefined, undefined, value => playing.push(value)); await settle()
  button(node, 'Original').click(); await settle()
  button(node, 'Singer').click(); await settle()
  finishOld?.(); await settle()
  expect(playing.at(-1)).toBe(true)
  rejectNew?.(new DOMException('Denied', 'NotAllowedError')); await settle()
  expect(playing.at(-1)).toBe(false)
})

it('releases cancelled ownership when the track changes before a requested file starts', async () => {
  const playing: boolean[] = []
  const node = mount(undefined, undefined, value => playing.push(value)); await settle()
  button(node, 'Original').click()
  currentTrack.value = 43; await settle()
  expect(playing).toEqual([true, false])
  expect(playedSources).toEqual([])
})

it('releases cancelled ownership when an unavailable version supersedes a pending file choice', async () => {
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, queued] })
  const playing: boolean[] = []
  const node = mount(undefined, undefined, value => playing.push(value)); await settle()
  button(node, 'Original').click(); button(node, 'Other singer · Queued').click(); await settle()
  expect(playing).toEqual([true, false])
  expect(playedSources).toEqual([])
})

it('releases an unmounted pending start and ignores its late completion', async () => {
  const playing: boolean[] = []
  let finish: (() => void) | undefined
  vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve }))
  const node = mount(undefined, undefined, value => playing.push(value)); await settle()
  button(node, 'Original').click(); await settle()
  expect(playing.at(-1)).toBe(true)
  app?.unmount(); app = undefined
  const before = [...playing]
  finish?.(); await settle()
  expect(playing).toEqual(before)
  expect(playing.at(-1)).toBe(false)
})

it('returns to the native source when the selected export becomes unavailable', async () => {
  exportsForOriginal()
  const node = mount(); await settle(); button(node, 'Original').click(); await settle(); formatButton(node, 'MP3').click(); await settle()
  exportsForOriginal([{ ...exported, status: 'failed', audio_url: null, error_code: 'export_modified' }])
  button(node, 'Refresh').click(); await settle()
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav')
  expect(formatButton(node, 'Source · WAV').getAttribute('aria-pressed')).toBe('true')
  expect(node.querySelector('a[download]')?.getAttribute('download')).toBe('original.wav')
  expect(playedSources).toEqual(['/original.wav', '/original.mp3'])
})

it('reports a browser play rejection once and lets the user explicitly retry', async () => {
  vi.useFakeTimers()
  vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new DOMException('Browser denied playback', 'NotAllowedError'))
  const node = mount(); await settle(); button(node, 'Original').click(); await settle()
  expect(node.querySelector('[role="alert"]')?.textContent).toContain('Could not play this audio')
  await vi.advanceTimersByTimeAsync(5000); await settle()
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce()
  button(node, 'Original').click(); await settle()
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2)
})

it('keeps one playback owner across main players', async () => {
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h('div', [h(TrackAudioVersions, { trackId: 42 }), h(TrackAudioVersions, { trackId: 42 })]) }).use(i18n)
  app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' }); app.mount(node); await settle()
  const [first, second] = node.querySelectorAll<HTMLElement>('section')
  if (!first || !second) throw new Error('Missing main players')
  button(first, 'Original').click(); await settle()
  expect(first.querySelector('button[aria-label="Pause"]')).not.toBeNull()
  button(second, 'Original').click(); await settle()
  expect(first.querySelector('button[aria-label="Pause"]')).toBeNull()
  expect(first.querySelector('button[aria-label="Play"]')).not.toBeNull()
  expect(second.querySelector('button[aria-label="Pause"]')).not.toBeNull()
})

const conversionProgress: VoiceJobProgress = { job_id: 'apply-42', kind: 'apply', status: 'queued', queued_at: 100, observed_at: 200, phase: 'waiting_gpu', queue_reason: 'voice_training', queue_label: 'SackJo22', phase_total: 0 }
it('keeps every active conversion and its queue reason visible while Original plays', async () => {
  vi.useFakeTimers(); vi.setSystemTime(200_000)
  const converting = { ...voice, status: 'running', audio_url: null, job_progress: { ...conversionProgress, job_id: 'other-42', status: 'running', phase: 'converting', phase_current: 2, phase_total: 8, phase_unit: 'chunks', estimated_phase_remaining_sec: 60 } } satisfies AudioVersion
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, { ...queued, job_progress: conversionProgress }, converting] })
  const node = mount(); await settle()
  expect(button(node, 'Original').getAttribute('aria-pressed')).toBe('true')
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav')
  expect(node.querySelectorAll('[data-voice-apply-progress]')).toHaveLength(2)
  expect(node.textContent).toContain('Waiting for voice training: SackJo22')
  expect(node.textContent).toContain('2 / 8 audio sections')
  expect(node.textContent).toContain('Elapsed: 1:40')
  expect(node.textContent).toContain('Current stage remaining: ≈ 1:00')
  expect(button(node, 'Other singer · Waiting for voice training')).toBeDefined()
  expect(button(node, 'Singer · Converting voice')).toBeDefined()
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, { ...queued, job_progress: { ...conversionProgress, status: 'running', phase: 'loading', queue_reason: '', queue_label: '' } }, converting] })
  await vi.advanceTimersByTimeAsync(2500); await settle()
  expect(node.textContent).toContain('Loading the voice model')
  expect(node.textContent).not.toContain('Waiting for voice training: SackJo22')
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav')
  app?.unmount(); app = undefined; expect(vi.getTimerCount()).toBe(0)
})
it('can cancel a queued conversion without selecting it or interrupting Original playback', async () => {
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, { ...queued, job_progress: conversionProgress }] })
  api.cancel.mockResolvedValue({ ...queued, status: 'cancelled', job_progress: { ...conversionProgress, status: 'cancelled', finished_at: 210 } })
  const node = mount(); await settle(); button(node, 'Original').click(); await settle()
  button(node, 'Cancel').click(); await settle()
  expect(api.cancel).toHaveBeenCalledWith(42, queued.id, expect.any(AbortSignal))
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav')
  expect(node.querySelectorAll('[data-voice-apply-progress]')).toHaveLength(0)
})
it('restores a completed selected voice elapsed time from its receipt and keeps it frozen', async () => {
  vi.useFakeTimers(); vi.setSystemTime(300_000)
  const completed = { ...voice, job_progress: { ...conversionProgress, status: 'done', phase: 'complete', finished_at: 250 } } satisfies AudioVersion
  api.list.mockResolvedValue({ track_id: 42, original_available: true, versions: [original, completed] })
  const node = mount(); await settle()
  expect(node.querySelector('[data-voice-apply-progress]')?.textContent).toContain('Elapsed: 2:30')
  expect(node.querySelector('[data-voice-apply-progress]')?.textContent).toContain('Done')
  expect(node.querySelector('[data-voice-apply-progress]')?.textContent).not.toContain('remaining')
  await vi.advanceTimersByTimeAsync(60_000); await settle()
  expect(node.querySelector('[data-voice-apply-progress]')?.textContent).toContain('Elapsed: 2:30')
  expect(vi.getTimerCount()).toBe(0)
})
