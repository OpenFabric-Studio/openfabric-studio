// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import ResultsFeed from '../../views/ace-step/ResultsFeed.vue'
import TrackFeed from '../../views/yue2/TrackFeed.vue'
import { useAceStepStore, type AceJob } from '../../stores/aceStep'
import { useYue2Store, type Yue2Job } from '../../stores/yue2'
import * as tracks from '../../api/tracks'
import { parseSavedTrack } from '../../api/contracts'
import { listActiveAudioTrackIds } from '../../api/trackActivity'
import { useTrackView } from '../../composables/useTrackView'
import { i18n, setLocale } from '../../i18n'
vi.mock('../../api/trackActivity', () => ({ listActiveAudioTrackIds: vi.fn().mockResolvedValue([]) }))
vi.mock('../../api/tracks', async original => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn(), deleteTrack: vi.fn(), setTrackFavorite: vi.fn() }))
vi.mock('./TrackAudioVersions.vue', () => ({ default: { props: ['trackId'], emits: ['playing', 'processing'], template: '<div data-player :data-track="trackId"><button data-play @click="$emit(\'playing\', true)">Play test</button><button data-pause @click="$emit(\'playing\', false)">Pause test</button><button data-process @click="$emit(\'processing\', true)">Process test</button></div>' } }))
vi.mock('./StemsPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('./MidiPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('./WaveformPlayer.vue', () => ({ default: { render: () => null } }))
function saved(id: number) { return parseSavedTrack({ id, short_id: id, model: 'yue2', title: `Song ${id}`, filename: 'song.wav', created_at: '2026-10-01T10:00:00Z', lyrics: '', seed: 4, duration_ms: 4000, wall_ms: null, params: {}, audio_url: '/song.wav', abc_url: null, stems: null, midi: null, is_favorite: id % 2 === 0 }) }
function ace(id: number): AceJob { return { id: `saved_${id}`, title: `Song ${id}`, status: 'done', createdAt: 1000 + id, lyrics: '', audioFormat: 'wav', batchSize: 1, finalized: true, dbIds: [id], shortIds: [id], audioUrls: [], progress: 100 } }
function yue(id: number): Yue2Job { return { id: `saved_${id}`, title: `Song ${id}`, style: 'folk, guitar', status: 'done', createdAt: 1000 + id, lyrics: '', seed: 4, precision: 'q8_0', cot: 'off', finalized: true, dbId: id, shortId: id, audioUrl: '/song.wav' } }
let app: App | undefined
beforeEach(() => { localStorage.clear(); setLocale('en'); useTrackView().setView('cards'); vi.clearAllMocks(); vi.mocked(listActiveAudioTrackIds).mockResolvedValue([]); vi.mocked(tracks.listTracks).mockResolvedValue(Array.from({ length: 12 }, (_, i) => saved(i + 1))); vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ track_ids: [] }), { status: 200 }))) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })
async function flush() { for (let i = 0; i < 15; i++) await nextTick() }
async function mount(kind: 'ace' | 'yue', size = 9, active = false) {
 const pinia = createPinia(); setActivePinia(pinia)
 const as = useAceStepStore(); const ys = useYue2Store()
 if (kind === 'ace') { as.jobs = Array.from({ length: size }, (_, i) => ace(i + 1)); as.historyLoaded = true; if (active) as.jobs.push({ ...ace(99), status: 'running', finalized: false }) }
 else { ys.jobs = Array.from({ length: size }, (_, i) => yue(i + 1)); ys.historyLoaded = true; if (active) ys.jobs.push({ ...yue(99), voiceApply: 'running' }) }
 app = createApp({ render: () => kind === 'ace' ? h(ResultsFeed) : h(TrackFeed) }).use(pinia).use(i18n)
 app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' }); app.mount(document.body.appendChild(document.createElement('div'))); await flush()
 return { as, ys }
}
function player(id: number): HTMLElement | null { return document.querySelector(`[data-player][data-track="${id}"]`) }
function click(selector: string) { const el = document.querySelector(selector); if (!(el instanceof HTMLButtonElement)) throw new Error(`Missing ${selector}`); el.click() }
it.each(['ace', 'yue'] as const)('paginates completed %s jobs, preserves active jobs and clamps after deletion', async kind => {
 const { as, ys } = await mount(kind, 9, true)
 expect(document.querySelectorAll('[data-player]')).toHaveLength(6)
 expect(player(99)).not.toBeNull()
 click('[data-pagination-next]'); await flush()
 expect(document.querySelectorAll('[data-player]')).toHaveLength(5)
 expect(player(99)).not.toBeNull()
 if (kind === 'ace') as.jobs = as.jobs.filter(job => job.id === 'saved_99' || job.createdAt >= 1005)
 else ys.jobs = ys.jobs.filter(job => job.id === 'saved_99' || job.createdAt >= 1005)
 await flush(); expect(document.querySelectorAll('[data-player]')).toHaveLength(6)
 expect(document.querySelector('[data-pagination-next]')).toBeNull()
 if (kind === 'ace') as.jobs.push(ace(10)); else ys.jobs.push(yue(10))
 await flush(); expect(document.querySelector('[aria-current="page"]')?.textContent).toBe('1')
})
it.each(['ace', 'yue'] as const)('keeps a playing %s player mounted after paging and filtering', async kind => {
 await mount(kind)
 const original = player(9); expect(original).not.toBeNull()
 click('[data-track="9"] [data-play]'); await flush()
 click('[data-pagination-next]'); await flush()
 expect(player(9)).toBe(original)
 const favorite = [...document.querySelectorAll('label')].find(el => el.textContent?.includes('Favorites only'))?.querySelector('input')
 if (!(favorite instanceof HTMLInputElement)) throw new Error('Missing favorite filter')
 favorite.click(); await flush(); expect(player(9)).toBe(original)
 click('[data-track="9"] [data-pause]'); await flush(); expect(player(9)).toBeNull()
})
it.each(['ace', 'yue'] as const)('keeps an additional processing %s version visible after paging', async kind => {
 await mount(kind); const original = player(9)
 click('[data-track="9"] [data-process]'); await flush(); click('[data-pagination-next]'); await flush()
 expect(player(9)).toBe(original)
})
it('applies favorites before pagination and resets when favorites or sort changes', async () => {
 await mount('yue', 12); click('[data-pagination-next]'); await flush()
 const favorite = [...document.querySelectorAll('label')].find(el => el.textContent?.includes('Favorites only'))?.querySelector('input')
 if (!(favorite instanceof HTMLInputElement)) throw new Error('Missing favorite filter')
 favorite.click(); await flush()
 expect([...document.querySelectorAll('[data-player]')].map(el => el.getAttribute('data-track'))).toEqual(['12', '10', '8', '6', '4'])
 expect(document.querySelector('[aria-current="page"]')?.textContent).toBe('1')
 click('[data-pagination-next]'); await flush()
 const oldest = [...document.querySelectorAll('button')].find(el => el.textContent?.trim() === 'Oldest first'); oldest?.click(); await flush()
 expect(document.querySelector('[aria-current="page"]')?.textContent).toBe('1')
 expect(player(2)).not.toBeNull()
})
it.each(['0', '-1', 'NaN', '7', 'Infinity'])('rejects unsupported persisted page size %s', async value => {
 localStorage.setItem('openfabric.pageSize', value); await mount('yue')
 expect(document.querySelectorAll('[data-player]')).toHaveLength(5)
})
it('remembers a supported page size across mounts', async () => {
 await mount('yue', 12)
 const select = document.querySelector<HTMLSelectElement>('[data-pagination-size]'); expect(select).not.toBeNull()
 if (!select) throw new Error('Missing page size')
 select.value = '10'; select.dispatchEvent(new Event('change', { bubbles: true })); await flush()
 expect(document.querySelectorAll('[data-player]')).toHaveLength(10)
 expect(localStorage.getItem('openfabric.pageSize')).toBe('10')
 app?.unmount(); document.body.replaceChildren(); await mount('yue', 12)
 expect(document.querySelectorAll('[data-player]')).toHaveLength(10)
})

it.each(['ace', 'yue'] as const)('restores off-page additional processing %s tracks from the backend activity list', async kind => {
 vi.mocked(listActiveAudioTrackIds).mockResolvedValue([1]); await mount(kind)
 expect(player(1)).not.toBeNull(); expect(document.querySelectorAll('[data-player]')).toHaveLength(6)
 click('[data-pagination-next]'); await flush(); expect(player(1)).not.toBeNull()
 const favorite = [...document.querySelectorAll('label')].find(el => el.textContent?.includes('Favorites only'))?.querySelector('input')
 if (!(favorite instanceof HTMLInputElement)) throw new Error('Missing favorite filter')
 favorite.click(); await flush(); expect(player(1)).not.toBeNull()
})
it('keeps the same playing player when switching to compact view', async () => {
 await mount('yue'); const original = player(9)
 click('[data-track="9"] [data-play]'); await flush()
 const compact = [...document.querySelectorAll('button')].find(el => el.textContent?.trim() === 'Compact')
 compact?.click(); await flush(); expect(player(9)).toBe(original)
})
it.each(['ace', 'yue'] as const)('shows additional processing details in compact %s view after reload', async kind => {
 useTrackView().setView('list')
 vi.mocked(listActiveAudioTrackIds).mockResolvedValue([1]); await mount(kind)
 expect(player(1)).not.toBeNull()
 expect(player(9)).toBeNull()
 click('[data-pagination-next]'); await flush(); expect(player(1)).not.toBeNull()
})
it('keeps a playing saved batch mounted across pages and favorites filtering', async () => {
 vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
 vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function(this: HTMLMediaElement) { this.dispatchEvent(new Event('pause')) })
 vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function(this: HTMLMediaElement) { this.dispatchEvent(new Event('play')); return Promise.resolve() })
 const { as } = await mount('ace')
 const job = as.jobs.find(job => job.id === 'saved_9'); if (!job) throw new Error('Missing batch')
 job.audioUrls = ['/batch-a.wav', '/batch-b.wav']; job.dbIds = [9, 11]; job.shortIds = [9, 11]; job.batchSize = 2; await flush()
 const audio = document.querySelector('audio[src="/batch-a.wav"]')
 const play = audio?.parentElement?.querySelector<HTMLButtonElement>('button[aria-label="Play"]'); if (!play) throw new Error('Missing batch play')
 play.click(); await flush(); click('[data-pagination-next]'); await flush()
 expect(document.querySelector('audio[src="/batch-a.wav"]')).toBe(audio)
 const favorite = [...document.querySelectorAll('label')].find(el => el.textContent?.includes('Favorites only'))?.querySelector('input')
 if (!(favorite instanceof HTMLInputElement)) throw new Error('Missing favorite filter')
 favorite.click(); await flush(); expect(document.querySelector('audio[src="/batch-a.wav"]')).toBe(audio)
})
it('resets to the first page when a date filter changes', async () => {
 const { ys } = await mount('yue', 12)
 for (const job of ys.jobs) job.createdAt = new Date('2026-10-01T10:00:00Z').getTime() + (job.dbId || 0)
 await flush(); click('[data-pagination-next]'); await flush()
 const from = document.querySelector<HTMLInputElement>('input[type=date]'); if (!from) throw new Error('Missing date filter')
 from.value = '2026-10-01'; from.dispatchEvent(new Event('change')); await flush()
 expect(document.querySelector('[aria-current="page"]')?.textContent).toBe('1')
})
