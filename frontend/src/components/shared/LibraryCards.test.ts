// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import JobCard from '../../views/ace-step/JobCard.vue'
import TrackCard from '../../views/yue2/TrackCard.vue'
import { useAceStepStore, type AceJob } from '../../stores/aceStep'
import { useYue2Store, type Yue2Job } from '../../stores/yue2'
import * as tracks from '../../api/tracks'
import { i18n, setLocale } from '../../i18n'
vi.mock('../../api/tracks', async original => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn().mockResolvedValue([]), deleteTrack: vi.fn(), renameTrack: vi.fn() }))
vi.mock('./TrackAudioVersions.vue', () => ({ default: { props: ['trackId'], emits: ['playing', 'processing'], template: '<div data-player :data-track="trackId"></div>' } }))
vi.mock('./StemsPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('./MidiPanel.vue', () => ({ default: { render: () => null } }))
const lyrics = '[Verse]\nHello (whisper) world\n\n[Chorus]\nSing again'
const ace: AceJob = { id: 'saved_7', title: 'iPhone song', status: 'done', createdAt: 1000, lyrics, params: { prompt: 'folk, acoustic guitar' }, audioFormat: 'wav', batchSize: 1, finalized: true, dbIds: [7], shortIds: [7], audioUrls: [], progress: 100 }
const yue: Yue2Job = { id: 'saved_7', title: 'iPhone song', style: 'folk, acoustic guitar', status: 'done', createdAt: 1000, lyrics, params: { style: 'folk, acoustic guitar' }, seed: 4, precision: 'q8_0', cot: 'off', finalized: true, dbId: 7, shortId: 7, audioUrl: '/song.wav' }
let app: App | undefined
beforeEach(() => { setLocale('en'); vi.clearAllMocks(); vi.spyOn(window, 'scrollTo').mockImplementation(() => {}); vi.mocked(tracks.listTracks).mockResolvedValue([]) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals(); vi.useRealTimers() })
async function flush() { for (let i = 0; i < 15; i++) await nextTick() }
async function mount(kind: 'ace' | 'yue') {
 const pinia = createPinia(); setActivePinia(pinia)
 const as = useAceStepStore(); const ys = useYue2Store(); as.jobs = [{ ...ace }]; ys.jobs = [{ ...yue }]
 app = createApp({ render: () => kind === 'ace' ? h(JobCard, { job: as.jobs[0], number: '7', view: 'cards' }) : h(TrackCard, { job: ys.jobs[0], number: '7', view: 'cards' }) }).use(pinia).use(i18n)
 app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot /></a>' }); app.mount(document.body.appendChild(document.createElement('div'))); await flush(); return { as, ys }
}
function findButton(text: string): HTMLButtonElement { const el = [...document.querySelectorAll('button')].find(item => item.textContent?.trim().endsWith(text) || item.getAttribute('aria-label') === text); if (!el) throw new Error(`Missing ${text}`); return el }
it.each(['ace', 'yue'] as const)('requires explicit confirmation before deleting a %s card and exposes failure', async kind => {
 const { as, ys } = await mount(kind)
 const remove = kind === 'ace' ? vi.spyOn(as, 'removeJob').mockRejectedValue(new Error('private stack')) : vi.spyOn(ys, 'deleteJob').mockRejectedValue(new Error('private stack'))
 findButton('Delete').click(); await flush(); expect(remove).not.toHaveBeenCalled()
 findButton('Click again to delete').click(); await flush(); expect(remove).toHaveBeenCalledOnce()
 expect(document.querySelector('[data-player]')).not.toBeNull()
 expect(document.querySelector('[role=alert]')?.textContent).toContain('Could not delete this track')
 expect(document.body.textContent).not.toContain('private stack')
})
it.each(['ace', 'yue'] as const)('renders structured lyrics and copies the exact source text on a %s card', async kind => {
 await mount(kind); findButton('Show text/params').click(); await flush()
 expect(document.querySelector('[data-lyrics-section]')?.textContent).toBe('Verse')
 expect(document.querySelector('[data-performance-mark]')?.textContent).toBe('(whisper)')
 const write = vi.fn().mockResolvedValue(undefined); vi.stubGlobal('navigator', { clipboard: { writeText: write } })
 findButton('Copy lyrics').click(); await flush(); expect(write).toHaveBeenCalledWith(lyrics)
 expect(findButton('Copied')).toBeDefined(); vi.unstubAllGlobals()
})
it('displays the YuE title but reuses its unchanged generation style', async () => {
 const { ys } = await mount('yue')
 expect(document.body.textContent).toContain('iPhone song')
 findButton('Copy params to form').click(); await flush()
 expect(ys.pendingParamsInsert?.style).toBe('folk, acoustic guitar')
})

it.each(['ace', 'yue'] as const)('shows a readable current date and a complete date tooltip on %s', async kind => {
 vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-01T12:00:00Z'))
 const { as, ys } = await mount(kind); const job = kind === 'ace' ? as.jobs[0] : ys.jobs[0]
 if (!job) throw new Error('Missing card')
 job.createdAt = Date.now(); await flush()
 const created = document.querySelector('[data-created]')
 expect(created?.textContent).toContain('Today')
 expect(created?.getAttribute('title')).toContain('2026')
 vi.useRealTimers()
})

it.each(['ace', 'yue'] as const)('shortens presentation without changing the %s title or its casing', async kind => {
 const { as, ys } = await mount(kind); const job = kind === 'ace' ? as.jobs[0] : ys.jobs[0]
 if (!job) throw new Error('Missing card')
 const full = 'iPhone song, acoustic guitar, piano, softly sung, a detailed orchestral arrangement with layered strings and harmonies'
 job.title = full; await flush()
 const title = document.querySelector('p[title]')
 expect(title?.textContent?.length).toBeLessThanOrEqual(80)
 expect(title?.textContent?.startsWith('iPhone')).toBe(true)
 expect(title?.getAttribute('title')).toBe(full)
 expect(job.title).toBe(full)
})
