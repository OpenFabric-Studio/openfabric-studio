// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import SettingsPage from './SettingsPage.vue'
vi.mock('./ModuleSetupPanel.vue', () => ({ default: { render: () => null } }))
import { i18n, setLocale } from '../../i18n'
import * as artist from '../../api/artistSettings'
vi.mock('../../api/artistSettings', () => ({ getArtistSettings: vi.fn(), saveArtistSettings: vi.fn() }))
vi.mock('../../api/audioSettings', () => ({ getAudioSettings: vi.fn().mockRejectedValue(new Error('Unavailable')), saveAudioSettings: vi.fn() }))
let app: App | undefined
beforeEach(() => {
  vi.clearAllMocks(); setLocale('en'); vi.mocked(artist.getArtistSettings).mockResolvedValue({ artist: 'Singer' }); vi.mocked(artist.saveArtistSettings).mockResolvedValue({ artist: 'New Singer' })
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ data_dir: '/library', pending_data_dir: '', restart_required: false, error: '', can_pick: false, folders: [] }))))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount() { const node = document.body.appendChild(document.createElement('div')); app = createApp(SettingsPage).use(i18n); app.mount(node); await settle(); return node }
function input(node: HTMLElement) { const field = node.querySelector<HTMLInputElement>('input[aria-label="Artist name"]'); if (!field) throw new Error('Missing artist input'); return field }
function save(node: HTMLElement) { const button = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === 'Save artist name'); if (!button) throw new Error('Missing artist save'); return button }
it('loads and saves artist metadata alongside the existing library and audio settings', async () => {
  const node = await mount(); expect(input(node).value).toBe('Singer'); expect(node.querySelector('input[spellcheck=false]')?.getAttribute('type')).toBe('text')
  input(node).value = 'New Singer'; input(node).dispatchEvent(new Event('input', { bubbles: true })); await settle(); save(node).click(); await settle()
  expect(artist.saveArtistSettings).toHaveBeenCalledWith('New Singer', '', expect.any(AbortSignal)); expect(node.textContent).toContain('Artist name saved.')
})
it('keeps newer edits made while a captured artist name is saving', async () => {
  let finish: (value: { artist: string }) => void = () => { throw new Error('No save') }
  vi.mocked(artist.saveArtistSettings).mockReturnValueOnce(new Promise(done => { finish = done }))
  const node = await mount(); input(node).value = 'Submitted'; input(node).dispatchEvent(new Event('input', { bubbles: true })); await settle(); save(node).click(); await settle()
  input(node).value = 'Newer edit'; input(node).dispatchEvent(new Event('input', { bubbles: true })); await settle(); finish({ artist: 'Submitted' }); await settle()
  expect(input(node).value).toBe('Newer edit'); expect(save(node).disabled).toBe(false)
})
