// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import MidiPanel from './MidiPanel.vue'
import * as api from '../../api/midi'
import { i18n, setLocale } from '../../i18n'

vi.mock('../../api/midi', () => ({ getMidiStatus: vi.fn(), startTranscription: vi.fn(), cancelTranscription: vi.fn(), deleteMidi: vi.fn() }))

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
  setLocale('en'); vi.useFakeTimers()
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(false))
  vi.mocked(api.getMidiStatus).mockResolvedValue(status(true))
  vi.mocked(api.startTranscription).mockResolvedValue(status(false))
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
