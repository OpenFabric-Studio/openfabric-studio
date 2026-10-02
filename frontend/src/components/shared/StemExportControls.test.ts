// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import StemExportControls from './StemExportControls.vue'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/stemExports'
import type { StemAudioExportResponse } from '../../api/contracts'
import { encodingSettings } from '../../views/settings/settingsTestFixtures'
vi.mock('../../api/stemExports', () => ({ list: vi.fn(), create: vi.fn(), get: vi.fn(), cancel: vi.fn(), retry: vi.fn() }))
const row: StemAudioExportResponse = { id: 'a'.repeat(32), track_id: 7, stem_name: 'vocals', format: 'mp3', status: 'queued', error_code: '', created_at: '2026-10-01', filename: null, audio_url: null, settings: encodingSettings() }
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); setLocale('en'); vi.mocked(api.list).mockResolvedValue([]); vi.mocked(api.create).mockResolvedValue(row); vi.mocked(api.get).mockResolvedValue({ ...row, status: 'done', audio_url: '/stem.mp3', filename: 'vocals.mp3' }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 8; index++) await nextTick() }
async function mount() { const container = document.body.appendChild(document.createElement('div')); app = createApp(StemExportControls, { trackId: 7, stemName: 'vocals' }).use(i18n); app.mount(container); await settle(); return container }
it('polls a prepared MP3 through completion and keeps the original WAV controls independent', async () => {
  const container = await mount(); container.querySelector('button')?.click(); await settle()
  expect(api.create).toHaveBeenCalledWith(7, 'vocals', expect.any(AbortSignal)); expect(container.textContent).toContain('Preparing MP3')
  await vi.advanceTimersByTimeAsync(2000); await settle(); expect(container.querySelector('a')?.getAttribute('download')).toBe('vocals.mp3'); expect(container.textContent).toContain('Download MP3')
})
it('invalidates an awaited export submission after teardown', async () => {
  let resolve = (_row: StemAudioExportResponse): void => { throw new Error('Uninitialized') }
  vi.mocked(api.create).mockReturnValue(new Promise(done => { resolve = done }))
  const container = await mount(); container.querySelector('button')?.click(); await settle(); app?.unmount(); app = undefined; resolve(row); await settle(); await vi.advanceTimersByTimeAsync(10000)
  expect(api.get).not.toHaveBeenCalled()
})
it('lets completed stems be exported again using current global settings', async () => {
  vi.mocked(api.list).mockResolvedValue([{ ...row, status: 'done', audio_url: '/old.mp3', filename: 'old.mp3' }])
  const container = await mount(); const create = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === 'Prepare MP3 with current settings')
  expect(create).toBeDefined(); create?.click(); await settle(); expect(api.create).toHaveBeenCalledWith(7, 'vocals', expect.any(AbortSignal))
})
it('restores the newest profile regardless of the list response ordering', async () => {
  vi.mocked(api.list).mockResolvedValue([{ ...row, status: 'done', audio_url: '/old.mp3', created_at: '2026-10-01T10:00:00Z' }, { ...row, id: 'b'.repeat(32), status: 'done', audio_url: '/new.mp3', created_at: '2026-10-01T11:00:00Z' }])
  const container = await mount(); expect(container.querySelector('a')?.getAttribute('href')).toBe('/new.mp3')
})
