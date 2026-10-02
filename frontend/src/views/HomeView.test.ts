// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import HomeView from './HomeView.vue'
import { i18n, setLocale } from '../i18n'
import * as tracks from '../api/tracks'
import * as projects from '../api/projects'
import type { SavedTrack } from '../api/contracts'
vi.mock('../api/tracks', () => ({ listTracks: vi.fn() }))
vi.mock('../api/projects', () => ({ listProjects: vi.fn() }))
vi.mock('../stores/orchestrator', () => ({ useOrchestratorStore: () => ({ statuses: {} }) }))
vi.mock('../composables/useModelSwitch', () => ({ MODEL_LABELS: { ace_step: 'ACE-Step', yue2: 'YuE2' }, useModelSwitch: () => ({ selectModel: vi.fn() }) }))
vi.mock('../components/shared/VoiceSelect.vue', () => ({ default: { template: '<p>Voice selection</p>' } }))
vi.mock('../components/shared/TrackAudioVersions.vue', () => ({ default: { props: ['trackId'], template: '<section :data-versions-track="trackId">Original / voice / format controls</section>' } }))
let app: App | undefined
function track(id: number): SavedTrack { return { id, title: `Track ${id}`, short_id: id, is_favorite: false, model: 'ace_step', created_at: `2026-10-${String(id).padStart(2, '0')}T10:00:00Z`, lyrics: '', seed: 1, params: {}, audio_url: `/api/tracks/${id}/audio`, filename: 'track.wav', duration_ms: null, wall_ms: null, abc_url: null, stems: null, midi: null } }
beforeEach(() => { vi.clearAllMocks(); setLocale('en'); vi.mocked(tracks.listTracks).mockResolvedValue([1, 6, 2, 5, 3, 4].map(track)); vi.mocked(projects.listProjects).mockResolvedValue([]) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: HomeView }, { path: '/editor', component: { render: () => null } }] }); await router.push('/')
  app = createApp(HomeView).use(router).use(i18n); const node = document.body.appendChild(document.createElement('div')); app.mount(node); await settle(); return node
}
it('shows the newest five tracks with version controls and retains voice selection', async () => {
  const node = await mount()
  expect([...node.querySelectorAll('[data-versions-track]')].map(element => element.getAttribute('data-versions-track'))).toEqual(['6', '5', '4', '3', '2'])
  expect(node.textContent).toContain('Voice selection')
})
it('reports a safe load failure and retries without losing existing home navigation', async () => {
  vi.mocked(tracks.listTracks).mockRejectedValueOnce(new Error('/private/library failed'))
  const node = await mount(); expect(node.textContent).toContain('Could not load recent tracks'); expect(node.textContent).not.toContain('/private/library')
  const retry = [...node.querySelectorAll('button')].find(button => button.textContent === 'Retry'); if (!retry) throw new Error('Missing retry')
  retry.click(); await settle(); expect(node.querySelectorAll('[data-versions-track]')).toHaveLength(5)
})
