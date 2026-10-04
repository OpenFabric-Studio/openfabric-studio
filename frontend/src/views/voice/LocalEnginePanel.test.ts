// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import LocalEnginePanel from './LocalEnginePanel.vue'
import { ApiError } from '../../api/http'
import * as api from '../../api/localEngines'
import type { LocalEngineCard } from '../../api/localEngines'
import { localEnginesEn } from '../../locales/localEngines'
import { speechWorkspaceEn } from '../../locales/speechWorkspace'

vi.mock('../../api/localEngines', async original => ({ ...await original<typeof import('../../api/localEngines')>(), listLocalEngines: vi.fn(), runKokoro: vi.fn(), runChatterbox: vi.fn(), runWan: vi.fn(), runRvc: vi.fn() }))

function card(id: LocalEngineCard['id'], installed: boolean, extra: Partial<LocalEngineCard> = {}): LocalEngineCard {
  const scripts = { kokoro: 'setup_kokoro.sh', chatterbox: 'setup_chatterbox.sh', wan22: 'setup_wan22.sh', rvc: 'setup_rvc.sh' }
  return { id, installed, setup_script: scripts[id], runtime: 'local', voices: id === 'kokoro' ? ['af_heart', 'bf_emma'] : [], languages: id === 'chatterbox' ? ['en', 'es'] : [], ...extra }
}

let app: App | undefined
beforeEach(() => { vi.resetAllMocks() })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })

async function mount(kind: 'speech' | 'singing' | 'picture') {
  app = createApp({ render: () => h(LocalEnginePanel, { kind, active: true }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { localEngines: localEnginesEn, speechWorkspace: speechWorkspaceEn, videoWorkspace: { playbackFailed: 'Could not play this video.' }, common: { download: 'Download' } } } }))
  const container = document.body.appendChild(document.createElement('div'))
  app.mount(container)
  for (let i = 0; i < 8; i++) await nextTick()
  return container
}

it('names the setup script when Kokoro and Chatterbox are missing and never offers Turbo', async () => {
  vi.mocked(api.listLocalEngines).mockResolvedValue({ video_engine: 'ltx', video_preference: 'ltx', note: 'Song videos stay on LTX.', engines: [card('kokoro', false), card('chatterbox', false), card('wan22', false), card('rvc', false)] })
  const container = await mount('speech')
  expect(container.textContent).toContain('does not clone a person')
  expect(container.textContent).toContain('Turbo is not available')
  expect(container.textContent).toContain('./setup_kokoro.sh')
  expect(container.textContent).toContain('./setup_chatterbox.sh')
  expect(container.textContent).not.toContain('14B')
  const models = [...container.querySelectorAll('[data-local-engine="chatterbox"] option')].map(option => option.textContent)
  expect(models).toEqual(['Original', 'Multilingual'])
  expect(container.querySelector('[data-local-engine="kokoro"] button')?.hasAttribute('disabled')).toBe(true)
})

it('plays Kokoro from the local media url and shows missing weight files', async () => {
  vi.mocked(api.listLocalEngines).mockResolvedValue({ video_engine: 'ltx', video_preference: 'ltx', note: '', engines: [card('kokoro', true), card('chatterbox', true), card('wan22', false), card('rvc', false)] })
  vi.mocked(api.runKokoro).mockRejectedValueOnce(new ApiError('Place these files yourself. OpenFabric does not download them for kokoro: kokoro-v1_0.pth.', 409))
  const container = await mount('speech')
  const text = container.querySelector('[data-local-engine="kokoro"] textarea')
  if (!(text instanceof HTMLTextAreaElement)) throw new Error('missing text')
  text.value = 'Hello from Kokoro.'
  text.dispatchEvent(new Event('input'))
  await nextTick()
  const form = container.querySelector('[data-local-engine="kokoro"]')
  form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  for (let i = 0; i < 8; i++) await nextTick()
  expect(container.textContent).toContain('kokoro-v1_0.pth')
  vi.mocked(api.runKokoro).mockResolvedValue({ status: 'completed', detail: 'kokoro wrote audio.', output_path: '/data/outputs/local-engines/kokoro/' + 'ab'.repeat(16) + '.wav', media_url: '/api/local-engines/kokoro/media/' + 'ab'.repeat(16), runtime: 'pytorch' })
  form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  for (let i = 0; i < 8; i++) await nextTick()
  expect(container.querySelector('audio')?.getAttribute('src')).toBe('/api/local-engines/kokoro/media/' + 'ab'.repeat(16))
})

it('keeps Wan on ti2v-5b for silent video and RVC on the singing screen', async () => {
  vi.mocked(api.listLocalEngines).mockResolvedValue({ video_engine: 'ltx', video_preference: 'ltx', note: '', engines: [card('kokoro', false), card('chatterbox', false), card('wan22', false), card('rvc', false)] })
  const picture = await mount('picture')
  expect(picture.textContent).toContain('ti2v-5b')
  expect(picture.textContent).toContain('./setup_wan22.sh')
  expect(picture.textContent).toContain('Song videos stay on LTX')
  expect(picture.textContent).toContain('14B, S2V, and Animate are not available')
  expect(picture.querySelector('[data-local-engine="wan22"] select')).toBeNull()
  app?.unmount(); app = undefined; document.body.replaceChildren()
  const singing = await mount('singing')
  expect(singing.textContent).toContain('CPU on a Mac')
  expect(singing.textContent).toContain('./setup_rvc.sh')
  expect(singing.textContent).toContain('Seed-VC stays')
})
