// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import HomeView from './HomeView.vue'
import { i18n, setLocale } from '../i18n'
import * as tracks from '../api/tracks'
import * as projects from '../api/projects'

const { selectModel } = vi.hoisted(() => ({ selectModel: vi.fn() }))
vi.mock('../api/tracks', () => ({ listTracks: vi.fn() }))
vi.mock('../api/projects', () => ({ listProjects: vi.fn() }))
vi.mock('../stores/orchestrator', () => ({ useOrchestratorStore: () => ({ statuses: {} }) }))
vi.mock('../composables/useModelSwitch', () => ({ MODEL_LABELS: { ace_step: 'ACE-Step', yue2: 'YuE2' }, useModelSwitch: () => ({ selectModel }) }))
vi.mock('../components/shared/VoiceSelect.vue', () => ({ default: { template: '<button>Voice selection</button>' } }))
vi.mock('../components/shared/TrackAudioVersions.vue', () => ({ default: { template: '<audio controls />' } }))

let app: App | undefined
beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.mocked(tracks.listTracks).mockResolvedValue([])
  vi.mocked(projects.listProjects).mockResolvedValue([])
  vi.stubGlobal('fetch', vi.fn())
})
afterEach(() => {
  app?.unmount()
  app = undefined
  document.body.replaceChildren()
  vi.unstubAllGlobals()
})
async function mount() {
  // An informative Home needs only translations, even without the router or app stores.
  app = createApp(HomeView).use(i18n)
  const node = document.body.appendChild(document.createElement('div'))
  app.mount(node)
  for (let i = 0; i < 8; i++) await nextTick()
  return node
}

it('explains each workspace and identifies its destination in the navigation', async () => {
  const node = await mount()
  expect([...node.querySelectorAll('h3')].map(heading => heading.textContent)).toEqual(['Music', 'Voices', 'Video', 'Editor'])
  for (const destination of ['Music', 'Audiobook', 'Voice Clone', 'Video', 'Editor']) expect(node.textContent).toContain(destination)
  expect(node.textContent).toContain('ACE-Step 1.5')
  expect(node.textContent).toContain('YuE2-3B')
  expect(node.textContent).toContain('Singing, speech and audiobooks')
  expect(node.textContent).toContain('timeline')
  expect(node.textContent).toContain('installed separately')
})

it('contains information without launch, playback, editing or navigation controls', async () => {
  const node = await mount()
  expect(node.querySelectorAll('button, input, select, textarea, form, audio, video, a[href], [role="button"], [tabindex="0"]')).toHaveLength(0)
  expect(node.textContent).not.toContain('Recent tracks')
  expect(node.textContent).not.toContain('Voice selection')
})

it('does not load libraries, start models or fetch data on mount', async () => {
  await mount()
  expect(tracks.listTracks).not.toHaveBeenCalled()
  expect(projects.listProjects).not.toHaveBeenCalled()
  expect(selectModel).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
})

it('remains informative when library services are unavailable', async () => {
  vi.mocked(tracks.listTracks).mockRejectedValue(new Error('/private/library unavailable'))
  vi.mocked(projects.listProjects).mockRejectedValue(new Error('/private/projects unavailable'))
  const node = await mount()
  expect(node.querySelector('h1')?.textContent).toContain('Music, voices')
  expect(node.textContent).toContain('Good to know')
  expect(node.textContent).toContain('Settings')
  expect(node.querySelector('[role="alert"], [role="status"]')).toBeNull()
  expect(node.textContent).not.toContain('/private/')
})

it('provides a clear heading outline and keeps illustration SVGs decorative', async () => {
  const node = await mount()
  expect(node.querySelectorAll('h1')).toHaveLength(1)
  expect([...node.querySelectorAll('h2')].map(heading => heading.textContent)).toEqual(['Find your workspace', 'Good to know'])
  const illustrations = node.querySelectorAll('svg')
  expect(illustrations.length).toBeGreaterThan(0)
  for (const illustration of illustrations) {
    expect(illustration.getAttribute('aria-hidden')).toBe('true')
    expect(illustration.getAttribute('focusable')).toBe('false')
  }
  expect(node.textContent).not.toContain('homeOverview.')
})
