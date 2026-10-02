// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import ResultsFeed from '../../views/ace-step/ResultsFeed.vue'
import TrackFeed from '../../views/yue2/TrackFeed.vue'
import { useAceStepStore, type AceJob } from '../../stores/aceStep'
import { useYue2Store, type Yue2Job } from '../../stores/yue2'
import * as tracks from '../../api/tracks'
import { parseSavedTrack, type AudioVersion, type AudioExportResponse } from '../../api/contracts'
import { useTrackView } from '../../composables/useTrackView'
import { i18n, setLocale } from '../../i18n'

const api = vi.hoisted(() => ({ versions: vi.fn(), exports: vi.fn() }))
vi.mock('../../api/audioVersions', () => ({ listAudioVersions: api.versions, createAudioVersion: vi.fn(), cancelAudioVersion: vi.fn(), retryAudioVersion: vi.fn() }))
vi.mock('../../api/audioExports', () => ({ listAudioExports: api.exports, createAudioExport: vi.fn(), cancelAudioExport: vi.fn(), retryAudioExport: vi.fn() }))
vi.mock('../../api/voices', async original => ({ ...await original<typeof import('../../api/voices')>(), listVoices: vi.fn().mockResolvedValue([]) }))
vi.mock('../../api/tracks', async original => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn() }))
vi.mock('../../api/trackActivity', () => ({ listActiveAudioTrackIds: vi.fn().mockResolvedValue([]) }))
vi.mock('../../composables/audioPlayback', async original => ({ ...await original<typeof import('../../composables/audioPlayback')>(), fetchAndComputePeaks: vi.fn().mockResolvedValue([.2, .7]) }))
vi.mock('./StemsPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('./MidiPanel.vue', () => ({ default: { render: () => null } }))

function version(id: number, voice = false): AudioVersion {
  return { id: (voice ? 'b' : 'a').repeat(32), track_id: id, kind: voice ? 'voice' : 'original', status: 'done', created_at: '2026-10-01T10:00:00Z', audio_url: `/${voice ? 'voice' : 'original'}-${id}.wav`, filename: 'song.wav', ...(voice ? { voice_name: 'Singer' } : {}) }
}
function exported(id: number, versionId: string): AudioExportResponse {
  return { id: 'c'.repeat(32), track_id: id, version_id: versionId, format: 'mp3', status: 'done', created_at: '2026-10-01T10:00:00Z', audio_url: `/export-${id}.mp3`, filename: 'song.mp3', settings: { mp3: { mode: 'cbr', bitrate_kbps: 320 } } }
}
function ace(id: number): AceJob { return { id: `saved_${id}`, title: `Song ${id}`, status: 'done', createdAt: 1000 + id, lyrics: '', audioFormat: 'wav', batchSize: 1, finalized: true, dbIds: [id], shortIds: [id], audioUrls: [], progress: 100 } }
function yue(id: number): Yue2Job { return { id: `saved_${id}`, title: `Song ${id}`, style: 'folk', status: 'done', createdAt: 1000 + id, lyrics: '', seed: 4, precision: 'q8_0', cot: 'off', finalized: true, dbId: id, shortId: id, audioUrl: `/original-${id}.wav` } }
let app: App | undefined
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); setLocale('en'); useTrackView().setView('cards')
  api.versions.mockImplementation((id: number) => Promise.resolve({ track_id: id, original_available: true, versions: [version(id), version(id, true)] }))
  api.exports.mockImplementation((id: number, versionId: string) => Promise.resolve({ exports: [exported(id, versionId)] }))
  vi.mocked(tracks.listTracks).mockResolvedValue(Array.from({ length: 9 }, (_, index) => parseSavedTrack({ id: index + 1, short_id: index + 1, model: 'yue2', title: 'Song', filename: 'song.wav', created_at: '2026-10-01T10:00:00Z', lyrics: '', seed: 4, duration_ms: 4000, wall_ms: null, params: {}, audio_url: '/song.wav', abc_url: null, stems: null, midi: null, is_favorite: false })))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) { this.dispatchEvent(new Event('pause')) })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) { this.dispatchEvent(new Event('play')); return Promise.resolve() })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks() })
async function settle() { for (let i = 0; i < 15; i++) await nextTick() }
async function mount(kind: 'ace' | 'yue') {
  const pinia = createPinia(); setActivePinia(pinia)
  if (kind === 'ace') { const store = useAceStepStore(); store.jobs = Array.from({ length: 9 }, (_, i) => ace(i + 1)); store.historyLoaded = true }
  else { const store = useYue2Store(); store.jobs = Array.from({ length: 9 }, (_, i) => yue(i + 1)); store.historyLoaded = true }
  app = createApp({ render: () => kind === 'ace' ? h(ResultsFeed) : h(TrackFeed) }).use(pinia).use(i18n)
  app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' })
  app.mount(document.body.appendChild(document.createElement('div'))); await settle()
}
function card(): HTMLElement {
  const element = document.querySelector<HTMLElement>('[data-library-job="saved_9"]')
  if (!element) throw new Error('Missing retained card')
  return element
}
function click(node: ParentNode, text: string) {
  const button = [...node.querySelectorAll('button')].find(item => item.textContent?.trim().startsWith(text))
  if (!button) throw new Error(`Missing ${text}`)
  button.click()
}
async function retainAcrossPage() {
  const retained = card(), audio = retained.querySelector('audio')
  click(retained, 'Original'); await settle()
  document.querySelector<HTMLButtonElement>('[data-pagination-next]')?.click(); await settle()
  expect(card()).toBe(retained); expect(card().querySelector('audio')).toBe(audio)
  return { retained, audio }
}

it.each(['ace', 'yue'] as const)('keeps the same real %s card and player when switching voice and export off page', async kind => {
  await mount(kind)
  const { retained, audio } = await retainAcrossPage()
  click(retained, 'Singer'); await settle()
  expect(document.querySelector('[data-library-job="saved_9"]')).toBe(retained)
  expect(retained.querySelector('audio')).toBe(audio)
  expect(audio?.getAttribute('src')).toBe('/voice-9.wav')
  expect(retained.querySelector('button[aria-label="Pause"]')).not.toBeNull()
  const formats = retained.querySelector('[role="group"][aria-label="Playback format"]')
  if (!formats) throw new Error('Missing playback formats')
  click(formats, 'MP3'); await settle()
  expect(document.querySelector('[data-library-job="saved_9"]')).toBe(retained)
  expect(retained.querySelector('audio')).toBe(audio)
  expect(audio?.getAttribute('src')).toBe('/export-9.mp3')
  expect(retained.querySelector('button[aria-label="Pause"]')).not.toBeNull()
  retained.querySelector<HTMLButtonElement>('button[aria-label="Pause"]')?.click(); await settle()
  expect(document.querySelector('[data-library-job="saved_9"]')).toBeNull()
})

it.each(['ace', 'yue'] as const)('releases an off-page %s card after its explicit source playback is rejected', async kind => {
  await mount(kind)
  const { retained } = await retainAcrossPage()
  vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce(new DOMException('Denied', 'NotAllowedError'))
  click(retained, 'Singer'); await settle()
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2)
  expect(document.querySelector('[data-library-job="saved_9"]')).toBeNull()
})
