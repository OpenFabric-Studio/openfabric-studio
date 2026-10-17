// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import MidiPanel from './MidiPanel.vue'
import * as api from '../../api/midi'
import { i18n, setLocale } from '../../i18n'
import * as termsApi from '../../api/musicTerms'
import { ApiError } from '../../api/http'
import type { MusicTermsSnapshot } from '../../api/generated'

vi.mock('../../api/midi', () => ({ getMidiStatus: vi.fn(), startTranscription: vi.fn(), cancelTranscription: vi.fn(), deleteMidi: vi.fn() }))
vi.mock('../../api/musicTerms', () => ({ getMusicTerms: vi.fn(), acceptMusicTerms: vi.fn(), updateMusicTermsSettings: vi.fn() }))

function terms(allowed = false): MusicTermsSnapshot {
  return { engine: 'yue2', operation: 'transcription', revision: 3, noncommercial_enabled: allowed, allowed, requirements: [{ component_id: 'sheet-sage', name: 'SheetSage weights', scope: 'model', restriction: 'noncommercial', source_url: 'https://example.org/terms', terms_digest: 'a'.repeat(64), acknowledged: allowed }] }
}

let app: App | undefined
function status(done: boolean): api.MidiStatus {
  return {
    available: ['full'], urls: done ? { full: '/full.mid' } : {},
    sources: { full: { status: done ? 'done' : 'idle', error: null }, vocals: { status: 'idle', error: null },
      drums: { status: 'idle', error: null }, bass: { status: 'idle', error: null }, other: { status: 'idle', error: null } },
  }
}
async function settle() { for (let index = 0; index < 12; index++) await nextTick() }
beforeEach(() => {
  vi.resetAllMocks(); setLocale('en'); vi.useFakeTimers()
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(false))
  vi.mocked(api.getMidiStatus).mockResolvedValue(status(true))
  vi.mocked(api.startTranscription).mockResolvedValue(status(false))
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms(true))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })
async function mount() {
  app = createApp(MidiPanel, { trackId: 9 }).use(i18n)
  const root = document.body.appendChild(document.createElement('div')); app.mount(root); await settle()
  root.querySelector('button')?.click(); await settle()
}
function button(text: string): HTMLButtonElement {
  const element = [...document.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!element) throw new Error(`Missing ${text}`)
  return element
}

it.each([false, true])('warns before overwriting MIDI and recreates only when accepted: %s', async accepted => {
  await mount(); vi.mocked(confirm).mockReturnValue(accepted)
  button('Recreate').click(); await settle()
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/replace.*Full mix.*MIDI.*file/i))
  if (accepted) expect(api.startTranscription).toHaveBeenCalledExactlyOnceWith(9, 'full', true)
  else {
    expect(api.startTranscription).not.toHaveBeenCalled()
    expect(api.cancelTranscription).not.toHaveBeenCalled()
    expect(api.deleteMidi).not.toHaveBeenCalled()
  }
})

it('starts initial MIDI recognition without a removal confirmation', async () => {
  vi.mocked(api.getMidiStatus).mockResolvedValue(status(false))
  await mount(); button('Recognize').click(); await settle()
  expect(confirm).not.toHaveBeenCalled()
  expect(api.startTranscription).toHaveBeenCalledExactlyOnceWith(9, 'full', false)
})

it('blocks new transcription until the real terms card receives explicit NC acknowledgement', async () => {
  vi.mocked(api.getMidiStatus).mockResolvedValue(status(false))
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms())
  vi.mocked(termsApi.acceptMusicTerms).mockResolvedValue(terms(true))
  await mount()
  expect(button('Recognize').disabled).toBe(true)
  button('Recognize').click(); await settle(); expect(api.startTranscription).not.toHaveBeenCalled()
  expect(termsApi.getMusicTerms).toHaveBeenCalledWith('yue2', 'transcription', expect.any(AbortSignal))
  for (const input of document.querySelectorAll<HTMLInputElement>('input[type=checkbox]')) input.click()
  await settle(); button('Accept reviewed terms').click(); await settle()
  expect(termsApi.acceptMusicTerms).toHaveBeenCalledWith({ engine: 'yue2', operation: 'transcription', expected_revision: 3, terms_digests: ['a'.repeat(64)], allow_noncommercial: true }, expect.any(AbortSignal))
  expect(button('Recognize').disabled).toBe(false)
  button('Recognize').click(); await settle(); expect(api.startTranscription).toHaveBeenCalledExactlyOnceWith(9, 'full', false)
})

it('keeps completed MIDI playback and downloads available while blocking recreation', async () => {
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms())
  await mount()
  expect(button('▶ Listen').disabled).toBe(false); expect(button('Download .mid').disabled).toBe(false)
  expect(button('Recreate').disabled).toBe(true)
  button('Recreate').click(); await settle(); expect(confirm).not.toHaveBeenCalled()
})

it('keeps cancelling an existing transcription available when terms are blocked', async () => {
  const running = status(false); running.sources.full.status = 'running'
  vi.mocked(api.getMidiStatus).mockResolvedValue(running)
  vi.mocked(api.cancelTranscription).mockResolvedValue(status(false))
  vi.mocked(termsApi.getMusicTerms).mockResolvedValue(terms())
  await mount(); expect(button('Cancel').disabled).toBe(false)
  button('Cancel').click(); await settle(); expect(api.cancelTranscription).toHaveBeenCalledWith(9, 'full')
})

it.each(['music_terms_required', 'music_noncommercial_disabled'])('translates the server terms error %s', async code => {
  vi.mocked(api.getMidiStatus).mockResolvedValue(status(false))
  vi.mocked(api.startTranscription).mockRejectedValue(new ApiError(code, 403))
  await mount(); button('Recognize').click(); await settle()
  expect(document.body.textContent).toContain('Review the publisher terms card for this operation')
  expect(document.body.textContent).not.toContain(code)
})
