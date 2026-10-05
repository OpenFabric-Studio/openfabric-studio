// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App as VueApp } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import { i18n, setLocale } from './i18n'
import * as api from './api/orchestrator'
import { useOrchestratorStore } from './stores/orchestrator'
import type { ModelRuntimeStatus, OrchestratorStatus } from './types'
import * as modulesApi from './api/modules'
import { useModulesStore } from './stores/modules'
import type { ModuleInventory, ModuleInfo } from './api/generated'

vi.mock('./api/openrouter', async original => ({ ...await original<typeof import('./api/openrouter')>(), getProviderStatus: vi.fn().mockResolvedValue({ enabled: false, estimate_limit_usd: 1, credential_configured: false, credential_source: 'none', secure_storage_available: false }) }))

vi.mock('./api/orchestrator', () => ({ getStatus: vi.fn(), switchModel: vi.fn(), stopActive: vi.fn() }))
vi.mock('./api/modules', () => ({ getModules: vi.fn(), listModuleJobs: vi.fn() }))
function moduleSnapshot(state: ModuleInfo['state'] = 'installed'): ModuleInventory {
  const ids: ModuleInfo['id'][] = ['ace_step', 'yue2', 'speech', 'singing', 'video', 'media']
  return { platform: 'darwin', architecture: 'arm64', acceleration: 'apple_silicon', managed_root: '/managed', free_bytes: 100, checked_at: 'today', modules: ids.map(id => ({ id, name: id, description: '', state, supported: state !== 'unsupported', managed: true, automation: 'automatic', dependencies: [], capabilities: [], evidence: [], actions: [] })) }
}
function headerLink(name: string): HTMLAnchorElement {
  const result = [...document.querySelectorAll<HTMLAnchorElement>('header a')].find(item => item.getAttribute('aria-label')?.startsWith(name + ':'))
  if (!result) throw new Error(`Missing header link ${name}`)
  return result
}
vi.mock('./composables/completionNotifications', () => ({
  completionNotifications: { unread: [] }, startCompletionPreferenceSync: vi.fn(), stopCompletionPreferenceSync: vi.fn(),
}))

let app: VueApp | undefined
let desktop = true
let media: MediaQueryList

function snapshot(status: ModelRuntimeStatus = 'stopped'): OrchestratorStatus {
  return {
    active_model: status === 'running' ? 'ace_step' : null,
    models: {
      ace_step: { id: 'ace_step', label: 'ACE-Step 1.5', status, error: null },
      yue2: { id: 'yue2', label: 'YuE2-3B', status: 'stopped', error: null },
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); setLocale('en'); desktop = true
  media = window.matchMedia('(min-width: 768px)')
  Object.defineProperty(media, 'matches', { configurable: true, get: () => desktop })
  vi.spyOn(window, 'matchMedia').mockReturnValue(media)
  vi.mocked(api.getStatus).mockResolvedValue(snapshot())
  vi.mocked(api.switchModel).mockResolvedValue(snapshot('running'))
  vi.mocked(modulesApi.getModules).mockResolvedValue(moduleSnapshot())
  vi.mocked(modulesApi.listModuleJobs).mockResolvedValue([])
})
afterEach(() => {
  app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks(); localStorage.clear()
})
async function settle() {
  for (let index = 0; index < 8; index++) await nextTick()
  // Router guards/history complete in a later task, beyond Vue's render queue.
  await new Promise<void>(resolve => setTimeout(resolve, 0)); await nextTick()
}
async function mount(path = '/voice-clone') {
  const view = defineComponent({ render: () => h('h1', 'Current workspace') })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'home', component: view }, { path: '/editor', name: 'editor-projects', component: view },
    { path: '/editor/:id', name: 'editor', component: view }, { path: '/voice-clone', name: 'voice-clone', component: view },
    { path: '/audiobooks', name: 'audiobooks', component: view }, { path: '/music', name: 'music', component: view },
    { path: '/video', name: 'video', component: view }, { path: '/settings', name: 'settings', component: view },
    { path: '/music/ace-step', name: 'ace-step', component: view }, { path: '/music/yue2', name: 'yue2', component: view },
    { path: '/ace-step/lora', name: 'ace-step-lora', component: view },
  ] })
  await router.push(path); await router.isReady()
  const pinia = createPinia()
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp(App).use(pinia).use(router).use(i18n); app.mount(node); await settle()
  return { node, router, store: useOrchestratorStore(pinia) }
}
function button(label: string, within: ParentNode = document): HTMLButtonElement {
  const result = [...within.querySelectorAll<HTMLButtonElement>('button')].find(item => item.getAttribute('aria-label') === label || item.textContent?.trim() === label)
  if (!result) throw new Error(`Missing button: ${label}`)
  return result
}
function sidebar(): HTMLElement {
  const result = document.querySelector<HTMLElement>('[data-app-sidebar]')
  if (!result) throw new Error('Missing app sidebar')
  return result
}
function link(path: string): HTMLAnchorElement {
  const result = sidebar().querySelector<HTMLAnchorElement>(`a[href="${path}"]`)
  if (!result) throw new Error(`Missing sidebar destination: ${path}`)
  return result
}
function key(target: Element, value: string, shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key: value, shiftKey, bubbles: true, cancelable: true })
  target.dispatchEvent(event); return event
}
function resize(isDesktop: boolean) { desktop = isDesktop; media.dispatchEvent(new Event('change')) }

it('groups all existing destinations and keeps Settings and Help in the sidebar', async () => {
  await mount()
  const nav = sidebar().querySelector('nav[aria-label="Main navigation"]')
  expect(nav).not.toBeNull()
  for (const group of ['Workspace', 'Create', 'Tools']) expect(nav?.querySelector(`section[aria-label="${group}"]`)).not.toBeNull()
  for (const path of ['/', '/editor', '/music', '/audiobooks', '/voice-clone', '/video', '/settings']) expect(link(path).getAttribute('aria-label')).toBeTruthy()
  expect([...nav?.querySelectorAll('section[aria-label="Create"] .navigation-item') ?? []].map(item => item.getAttribute('aria-label'))).toEqual(['Music', 'Audiobook', 'Video'])
  expect([...nav?.querySelectorAll('section[aria-label="Tools"] .navigation-item') ?? []].map(item => item.getAttribute('aria-label'))).toEqual(['LoRA Training', 'Voice Clone'])
  expect(sidebar().querySelector('[data-model]')).toBeNull()
  expect(button('Help', sidebar())).toBeDefined()
  expect(document.querySelector('header nav')).toBeNull()
})

it('marks the correct destination for nested editor routes and history changes', async () => {
  const { router } = await mount('/editor/project-1')
  expect(link('/editor').getAttribute('aria-current')).toBe('page')
  expect(link('/').getAttribute('aria-current')).toBeNull()
  await router.push('/video'); await settle()
  expect(link('/video').getAttribute('aria-current')).toBe('page')
  router.back(); await settle()
  expect(link('/editor').getAttribute('aria-current')).toBe('page')
})

it('folds into labelled icons and remembers the preference after remount', async () => {
  await mount(); button('Collapse navigation').click(); await settle()
  expect(button('Expand navigation').getAttribute('aria-expanded')).toBe('false')
  expect(link('/voice-clone').getAttribute('aria-label')).toBe('Voice Clone')
  app?.unmount(); app = undefined; document.body.replaceChildren()
  await mount(); expect(button('Expand navigation')).toBeDefined()
  button('Expand navigation').click(); await settle(); expect(button('Collapse navigation')).toBeDefined()
})

it.each(['invalid', '{"collapsed":true}', 'null'])('ignores malformed navigation preference %s', async value => {
  localStorage.setItem('openfabric_navigation', value); await mount()
  expect(button('Collapse navigation')).toBeDefined()
})

it('keeps navigation usable when preference storage is blocked', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  await mount(); button('Collapse navigation').click(); await settle()
  expect(button('Expand navigation')).toBeDefined()
})

it('opens a narrow-screen drawer, wraps focus, closes with Escape and restores the opener', async () => {
  desktop = false; await mount()
  const opener = button('Open navigation'); opener.focus(); opener.click(); await settle()
  const drawer = document.querySelector<HTMLElement>('[role="dialog"][aria-label="Main navigation"]')
  expect(drawer).not.toBeNull(); expect(drawer?.contains(document.activeElement)).toBe(true)
  const last = button('Help', sidebar()); last.focus()
  expect(key(last, 'Tab').defaultPrevented).toBe(true)
  expect(document.activeElement).toBe(button('Close navigation', sidebar()))
  key(document.activeElement ?? last, 'Escape'); await settle()
  expect(document.querySelector('[role="dialog"][aria-label="Main navigation"]')).toBeNull()
  expect(document.activeElement).toBe(opener)
})

it('closes the mobile drawer when navigating and retains the desktop collapse preference', async () => {
  await mount(); button('Collapse navigation').click(); await settle()
  resize(false); await settle(); button('Open navigation').click(); await settle()
  link('/video').click(); await settle()
  expect(document.querySelector('[role="dialog"][aria-label="Main navigation"]')).toBeNull()
  resize(true); await settle(); expect(button('Expand navigation')).toBeDefined()
})

it('releases drawer focus ownership on desktop resize and teardown', async () => {
  desktop = false; await mount(); button('Open navigation').click(); await settle()
  resize(true); await settle()
  expect(document.querySelector('[aria-modal="true"]')).toBeNull()
  resize(false); await settle(); button('Open navigation').click(); await settle()
  app?.unmount(); app = undefined
  const outside = document.body.appendChild(document.createElement('button')); outside.focus()
  expect(key(outside, 'Tab').defaultPrevented).toBe(false)
  resize(true); await settle(); expect(document.activeElement).toBe(outside)
})

it('opens module settings when tapped without starting an engine', async () => {
  const { router } = await mount()
  headerLink('ACE-Step').click(); await settle()
  expect(api.switchModel).not.toHaveBeenCalled()
  expect(router.currentRoute.value.name).toBe('settings')
  expect(router.currentRoute.value.hash).toBe('#module-ace_step')
  expect(document.querySelector('[role="tooltip"]')?.textContent).toContain('ACE-Step: Installed')
})

it('shows unknown until an initial runtime snapshot is available', async () => {
  vi.mocked(modulesApi.getModules).mockReturnValue(new Promise(() => {}))
  await mount()
  expect(headerLink('ACE-Step').getAttribute('aria-label')).toContain('Status unavailable')
})

it.each<ModuleInfo['state']>(['ready', 'installed', 'partial', 'missing', 'unsupported'])('exposes the %s module state in six labelled header icons', async state => {
  await mount(); useModulesStore().inventory = moduleSnapshot(state); await settle()
  const labels = { ready: 'Ready', installed: 'Installed', partial: 'Setup incomplete', missing: 'Not installed', unsupported: 'Unavailable on this computer' }
  expect(headerLink('ACE-Step').getAttribute('aria-label')).toContain(labels[state])
  for (const name of ['ACE-Step', 'YuE', 'Speech', 'Singing', 'Video', 'Tools']) expect(headerLink(name)).toBeDefined()
  expect(document.querySelectorAll('header a[href*="module-"]')).toHaveLength(6)
  expect(document.querySelector('header a[href="/settings#providers"]')?.getAttribute('aria-label')).toContain('OpenRouter disabled')
})

it('shows status tooltips for keyboard focus and dismisses them with Escape', async () => {
  await mount()
  const status = headerLink('ACE-Step')
  status.focus(); await settle()
  const tooltip = document.querySelector('[role="tooltip"]')
  expect(tooltip?.textContent).toContain('ACE-Step: Installed')
  expect(status.getAttribute('aria-describedby')).toBe(tooltip?.id)
  key(status, 'Escape'); await settle()
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  expect(document.activeElement).toBe(status)
})

it('does not expose a raw engine switch error in the compact header', async () => {
  const { store } = await mount(); store.switchError = '/private/engine/token-secret: traceback'; await settle()
  expect(document.body.textContent).not.toContain('token-secret')
  expect(button('Music engine could not start. Retry in Music or check Settings.')).toBeDefined()
})

it('keeps LoRA discoverable with an explicit running-engine prerequisite', async () => {
  const { router, store } = await mount()
  const training = button('LoRA Training', sidebar())
  expect(training.getAttribute('aria-disabled')).toBe('true')
  training.focus(); await settle(); expect(document.querySelector('[role="tooltip"]')?.textContent).toContain('Open Music and start ACE-Step 1.5 to train a LoRA.')
  training.click(); await settle(); expect(router.currentRoute.value.name).toBe('voice-clone')
  store._applySnapshot(snapshot('running')); await settle(); training.click(); await settle()
  expect(router.currentRoute.value.name).toBe('ace-step-lora')
})

it('keeps Help focus ownership nested inside the mobile drawer', async () => {
  desktop = false; await mount(); button('Open navigation').click(); await settle()
  const help = button('Help', sidebar()); help.focus(); help.click(); await settle()
  const dialogs = document.querySelectorAll('[role="dialog"]')
  expect(dialogs).toHaveLength(2)
  const top = dialogs[1]; if (!top) throw new Error('Missing Help dialog')
  key(top, 'Escape'); await settle()
  expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1)
  expect(document.activeElement).toBe(help)
  key(help, 'Escape'); await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
})

it('provides a skip link that focuses the main workspace', async () => {
  await mount()
  const skip = document.querySelector<HTMLAnchorElement>('a[href="#main-content"]')
  expect(skip).not.toBeNull(); skip?.click(); await settle()
  expect(document.activeElement?.id).toBe('main-content')
})

it('removes a hovered tooltip when history hides the mobile drawer', async () => {
  desktop = false; const { router } = await mount()
  button('Open navigation').click(); await settle()
  const engine = button('LoRA Training', sidebar())
  if (!engine?.parentElement) throw new Error('Missing training tooltip trigger')
  engine.focus(); engine.parentElement.dispatchEvent(new MouseEvent('mouseenter')); await settle()
  expect(document.querySelector('[role="tooltip"]')).not.toBeNull()
  await router.push('/video'); await settle()
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
})

it('dismisses a tapped status tooltip with a second tap or an outside pointer action', async () => {
  await mount()
  const status = headerLink('ACE-Step')
  status.focus(); status.click(); await settle(); expect(document.querySelector('[role="tooltip"]')).not.toBeNull()
  status.click(); await settle(); expect(document.querySelector('[role="tooltip"]')).toBeNull()
  status.click(); await settle(); expect(document.querySelector('[role="tooltip"]')).not.toBeNull()
  document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); await settle()
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
})

it('keeps the unavailable training explanation visible after a touch-style tap', async () => {
  await mount()
  const training = button('LoRA Training', sidebar()); training.focus(); training.click(); await settle()
  expect(document.querySelector('[role="tooltip"]')?.textContent).toContain('Open Music and start ACE-Step 1.5 to train a LoRA.')
  expect(api.switchModel).not.toHaveBeenCalled()
})

it('dismisses a drawer tooltip with Escape before closing the drawer on the next Escape', async () => {
  desktop = false; await mount(); button('Open navigation').click(); await settle()
  const training = button('LoRA Training', sidebar()); training.focus(); await settle()
  expect(document.querySelector('[role="tooltip"]')).not.toBeNull()
  key(training, 'Escape'); await settle()
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  expect(document.querySelector('[role="dialog"][aria-label="Main navigation"]')).not.toBeNull()
  key(training, 'Escape'); await settle()
  expect(document.querySelector('[role="dialog"][aria-label="Main navigation"]')).toBeNull()
})

it.each([true, false])('suppresses background tooltips while Help owns focus (desktop=%s)', async isDesktop => {
  desktop = isDesktop; await mount()
  if (!desktop) { button('Open navigation').click(); await settle() }
  const engine = button('LoRA Training', sidebar())
  if (!engine?.parentElement) throw new Error('Missing training tooltip trigger')
  engine.parentElement.dispatchEvent(new MouseEvent('mouseenter')); await settle()
  expect(document.querySelector('[role="tooltip"]')).not.toBeNull()
  const help = button('Help', sidebar()); help.focus(); help.click(); await settle()
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  const dialogs = [...document.querySelectorAll('[role="dialog"]')]
  const top = dialogs.at(-1); if (!top) throw new Error('Missing Help dialog')
  key(top, 'Escape'); await settle()
  expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(desktop ? 0 : 1)
  expect(document.activeElement).toBe(help)
})

it('opens Music without starting an engine and marks both model routes as Music', async () => {
  const { router } = await mount()
  link('/music').click(); await settle()
  expect(router.currentRoute.value.name).toBe('music')
  expect(api.switchModel).not.toHaveBeenCalled()
  for (const path of ['/music/ace-step', '/music/yue2']) {
    await router.push(path); await settle()
    expect(link('/music').getAttribute('aria-current')).toBe('page')
  }
})
it('keeps Audiobook and Voice Clone selected independently', async () => {
  const { router } = await mount('/audiobooks')
  expect(link('/audiobooks').getAttribute('aria-current')).toBe('page')
  expect(link('/voice-clone').getAttribute('aria-current')).toBeNull()
  await router.push('/voice-clone?mode=speech'); await settle()
  expect(link('/voice-clone').getAttribute('aria-current')).toBe('page')
  expect(link('/audiobooks').getAttribute('aria-current')).toBeNull()
})
