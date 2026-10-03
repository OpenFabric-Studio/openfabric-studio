// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App, type Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import SettingsPage from './SettingsPage.vue'
import HomeView from '../HomeView.vue'
import AppSidebar from '../../components/shared/AppSidebar.vue'
import appRouter from '../../router'
import * as audioApi from '../../api/audioSettings'
import { i18n, setLocale } from '../../i18n'
import { audioSettingsResponse } from './settingsTestFixtures'
import type { CompleteAudioSettingsResponse } from '../../api/audioSettings'
vi.mock('./ModuleSetupPanel.vue', () => ({ default: defineComponent({ render: () => null }) }))

vi.mock('../../api/generationLibrary', async original => ({ ...await original<typeof import('../../api/generationLibrary')>(), getSettings: vi.fn().mockResolvedValue({ history_limit: 100, revision: 1 }) }))
vi.mock('../../api/audioSettings', () => ({ getAudioSettings: vi.fn(), saveAudioSettings: vi.fn() }))
vi.mock('../../api/projects', () => ({ listProjects: vi.fn().mockResolvedValue([]) }))
vi.mock('../../api/voices', async (original) => ({ ...await original<typeof import('../../api/voices')>(), listVoices: vi.fn().mockResolvedValue([]) }))
vi.mock('../../stores/orchestrator', () => ({ useOrchestratorStore: () => ({ statuses: {}, switchError: '' }) }))
vi.mock('../../composables/useModelSwitch', async (original) => ({ ...await original<typeof import('../../composables/useModelSwitch')>(), useModelSwitch: () => ({ selectModel: vi.fn() }) }))

let app: App | undefined
const ExpandedSidebar = defineComponent({ render: () => h(AppSidebar, { collapsed: false, mobile: false, active: true }) })
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); localStorage.clear(); setLocale('en')
  vi.mocked(audioApi.getAudioSettings).mockResolvedValue(audioSettingsResponse())
  vi.mocked(audioApi.saveAudioSettings).mockImplementation(async (settings) => {
    const response = audioSettingsResponse()
    return { ...response, settings: {
      mp3: { ...response.settings.mp3, ...settings.mp3 },
      wav: { ...response.settings.wav, ...settings.wav },
      flac: { ...response.settings.flac, ...settings.flac },
    } }
  })
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>().mockImplementation(async input => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const data = url === '/api/tracks' ? { data: [] } : url === '/api/settings' ? { artist: '' } : { data_dir: '/test/library', pending_data_dir: '', restart_required: false, error: '', can_pick: false, folders: [] }
    return new Response(JSON.stringify(data))
  }))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })
async function settle() { for (let index = 0; index < 6; index++) await nextTick() }
async function mount(component: Component = SettingsPage, path = '/') {
  app?.unmount()
  const router = createRouter({ history: createMemoryHistory(), routes: ['/', '/settings', '/editor', '/voice-clone', '/video', '/ace-step', '/yue2', '/ace-step/lora'].map((route) => ({ path: route, name: route, component: { render: () => null } })) })
  await router.push(path)
  app = createApp(component).use(i18n).use(router)
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle(); return container
}
function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll('button')].find((node) => node.textContent?.trim() === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
function select(container: HTMLElement, name: string) {
  const found = [...container.querySelectorAll('select')].find((node) => node.getAttribute('aria-label') === name)
  if (!found) throw new Error(`Missing named select: ${name}`)
  return found
}
async function change(container: HTMLElement, name: string, value: string) {
  const field = select(container, name); field.value = value; field.dispatchEvent(new Event('change', { bubbles: true })); await settle(); return field
}
function deferred() {
  let resolve: (value: CompleteAudioSettingsResponse) => void = () => { throw new Error('Not initialized') }
  let reject: (error: Error) => void = () => { throw new Error('Not initialized') }
  const promise = new Promise<CompleteAudioSettingsResponse>((success, failure) => { resolve = success; reject = failure })
  return { promise, resolve, reject }
}

it('loads explicit per-format controls and explains future exports and source limits', async () => {
  const container = await mount()
  expect(container.querySelector('h1')?.textContent).toBe('Settings')
  expect(select(container, 'MP3 encoding mode').value).toBe('cbr')
  expect(select(container, 'MP3 bitrate').value).toBe('320')
  expect(select(container, 'WAV bit depth').value).toBe('24')
  expect(select(container, 'FLAC bit depth').value).toBe('24')
  expect(select(container, 'FLAC compression level').value).toBe('5')
  for (const format of ['MP3', 'WAV', 'FLAC']) {
    expect(select(container, `${format} sample rate`).value).toBe('48000')
    expect(select(container, `${format} channels`).value).toBe('2')
  }
  expect(button(container, 'Save audio settings').disabled).toBe(true)
  expect(container.textContent).toContain('future exports'); expect(container.textContent).toContain('queued exports keep'); expect(container.textContent).toContain('cannot restore')
  await change(container, 'MP3 encoding mode', 'vbr')
  expect(select(container, 'MP3 VBR quality').value).toBe('2')
})

it('shows loading and a safe failed-load retry without inventing loaded settings', async () => {
  const load = deferred(); vi.mocked(audioApi.getAudioSettings).mockReturnValueOnce(load.promise)
  const container = await mount()
  expect(container.textContent).toContain('Loading audio settings'); expect(container.querySelector('select')).toBeNull()
  load.reject(new Error('/secret/config failed')); await settle()
  expect(container.querySelector('[role=alert]')?.textContent).toContain('Could not load audio settings'); expect(container.textContent).not.toContain('/secret/config')
  button(container, 'Retry loading').click(); await settle()
  expect(select(container, 'MP3 bitrate').value).toBe('320')
})

it('saves a captured backend-validated encoding draft and reports completion', async () => {
  const container = await mount()
  await change(container, 'MP3 bitrate', '192'); await change(container, 'WAV bit depth', '32'); await change(container, 'FLAC compression level', '8')
  button(container, 'Save audio settings').click(); await settle()
  expect(audioApi.saveAudioSettings).toHaveBeenCalledWith(expect.objectContaining({ mp3: expect.objectContaining({ bitrate_kbps: 192 }), wav: expect.objectContaining({ bit_depth: 32 }), flac: expect.objectContaining({ compression_level: 8 }) }), expect.any(AbortSignal))
  expect(container.textContent).toContain('Audio settings saved'); expect(button(container, 'Save audio settings').disabled).toBe(true)
})

it('stages server defaults without writing until Save is explicitly pressed', async () => {
  const response = audioSettingsResponse(); response.settings.mp3.bitrate_kbps = 128; response.settings.wav.bit_depth = 16
  vi.mocked(audioApi.getAudioSettings).mockResolvedValue(response)
  const container = await mount(); button(container, 'Reset to defaults').click(); await settle()
  expect(select(container, 'MP3 bitrate').value).toBe('320'); expect(select(container, 'WAV bit depth').value).toBe('24'); expect(audioApi.saveAudioSettings).not.toHaveBeenCalled()
  expect(container.textContent).toContain('Save to apply'); button(container, 'Save audio settings').click(); await settle()
  expect(audioApi.saveAudioSettings).toHaveBeenCalledWith(response.defaults, expect.any(AbortSignal))
})

it('does not ask users to save when the saved settings already equal the defaults', async () => {
  const container = await mount(); button(container, 'Reset to defaults').click(); await settle()
  expect(container.textContent).toContain('Saved settings already use the defaults')
  expect(container.textContent).not.toContain('Save to apply'); expect(button(container, 'Save audio settings').disabled).toBe(true)
  expect(audioApi.saveAudioSettings).not.toHaveBeenCalled()
})

it('retains the draft after a failed save and presents a translated generic error', async () => {
  vi.mocked(audioApi.saveAudioSettings).mockRejectedValue(new Error('secret traceback'))
  const container = await mount(); await change(container, 'MP3 bitrate', '192'); button(container, 'Save audio settings').click(); await settle()
  expect(select(container, 'MP3 bitrate').value).toBe('192'); expect(button(container, 'Save audio settings').disabled).toBe(false)
  expect(container.querySelector('[role=alert]')?.textContent).toContain('Could not save audio settings'); expect(container.textContent).not.toContain('secret traceback')
})

it('reserves one save and preserves edits made while its older snapshot is being saved', async () => {
  const save = deferred(); vi.mocked(audioApi.saveAudioSettings).mockReturnValueOnce(save.promise)
  const container = await mount(); await change(container, 'MP3 bitrate', '192')
  const action = button(container, 'Save audio settings'); action.click(); action.click(); await settle()
  expect(audioApi.saveAudioSettings).toHaveBeenCalledOnce()
  await change(container, 'MP3 bitrate', '256')
  const response = audioSettingsResponse(); response.settings.mp3.bitrate_kbps = 192; save.resolve(response); await settle()
  expect(select(container, 'MP3 bitrate').value).toBe('256'); expect(button(container, 'Save audio settings').disabled).toBe(false); expect(container.textContent).toContain('unsaved changes')
})

it('preserves edits made during a reload while refreshing the saved baseline and defaults', async () => {
  const load = deferred(); const container = await mount()
  vi.mocked(audioApi.getAudioSettings).mockReturnValueOnce(load.promise)
  button(container, 'Reload saved settings').click(); await settle()
  await change(container, 'MP3 bitrate', '256')
  const response = audioSettingsResponse(); response.settings.mp3.bitrate_kbps = 128; load.resolve(response); await settle()
  expect(select(container, 'MP3 bitrate').value).toBe('256'); expect(button(container, 'Save audio settings').disabled).toBe(false)
})

it('blocks invalid runtime settings instead of submitting an unchecked draft', async () => {
  const container = await mount(); await change(container, 'MP3 encoding mode', 'vbr')
  const quality = select(container, 'MP3 VBR quality'); const invalid = document.createElement('option'); invalid.value = '10'; invalid.textContent = 'Invalid'; quality.append(invalid); await change(container, 'MP3 VBR quality', '10')
  expect(button(container, 'Save audio settings').disabled).toBe(true); expect(container.textContent).toContain('Choose valid encoding values'); expect(audioApi.saveAudioSettings).not.toHaveBeenCalled()
})

it.each(['load', 'save'] as const)('aborts an outstanding %s on teardown and ignores its late response', async (kind) => {
  const task = deferred()
  if (kind === 'load') vi.mocked(audioApi.getAudioSettings).mockReturnValueOnce(task.promise)
  else vi.mocked(audioApi.saveAudioSettings).mockReturnValueOnce(task.promise)
  const container = await mount()
  if (kind === 'save') { await change(container, 'MP3 bitrate', '192'); button(container, 'Save audio settings').click(); await settle() }
  const signal = kind === 'load' ? vi.mocked(audioApi.getAudioSettings).mock.calls[0]?.[0] : vi.mocked(audioApi.saveAudioSettings).mock.calls[0]?.[1]
  expect(signal?.aborted).toBe(false)
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  task.resolve(audioSettingsResponse()); await settle(); expect(container.textContent).toBe('')
})

it('moves the existing library migration workflow from Home into Settings', async () => {
  const home = await mount(HomeView)
  expect(home.textContent).not.toContain(i18n.global.t('dataFolder.title'))
  const settings = await mount()
  expect(settings.textContent).toContain(i18n.global.t('dataFolder.title'))
  await settle()
  const folder = settings.querySelector('input[spellcheck=false]')
  if (!(folder instanceof HTMLInputElement)) throw new Error('Missing library folder field')
  expect(folder.value).toBe('/test/library'); expect(folder.getAttribute('spellcheck')).toBe('false')
})

it('allows full long library catalog paths to wrap instead of widening the mobile page', async () => {
  const path = `/temporary/${'long-library-name-'.repeat(20)}`
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ data_dir: path, pending_data_dir: '', restart_required: false, error: '', can_pick: false, folders: [{ path: 'files', key: 'tracks' }] }))))
  const container = await mount(); await settle()
  const label = container.querySelector('li span.font-mono')
  expect(label?.textContent).toBe(`${path}/files`)
  expect(label?.classList.contains('[overflow-wrap:anywhere]')).toBe(true)
})

it('registers a lazy Settings route and highlights its translated sidebar link', async () => {
  const route = appRouter.getRoutes().find((entry) => entry.path === '/settings')
  expect(route?.name).toBe('settings'); expect(typeof route?.components?.default).toBe('function')
  const container = await mount(ExpandedSidebar, '/settings')
  const link = [...container.querySelectorAll('a')].find((node) => node.textContent?.trim() === 'Settings')
  expect(link?.getAttribute('href')).toBe('/settings'); expect(link?.getAttribute('aria-current')).toBe('page'); expect(link?.getAttribute('aria-label')).toBe('Settings')
})

it('opens global help while preserving all fork navigation routes', async () => {
  const sidebar = await mount(ExpandedSidebar, '/settings')
  for (const path of ['/settings', '/editor', '/voice-clone', '/video']) expect(sidebar.querySelector(`a[href="${path}"]`)).not.toBeNull()
  const help = button(sidebar, 'Help'); help.focus(); help.click(); await settle()
  const dialog = document.querySelector('[role="dialog"][aria-label="Using OpenFabric Studio"]')
  expect(dialog?.textContent).toContain('separate voice versions'); expect(dialog?.textContent).toContain('Video Studio')
  dialog?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await settle()
  expect(document.querySelector('[aria-label="Using OpenFabric Studio"]')).toBeNull(); expect(document.activeElement).toBe(help)
})
