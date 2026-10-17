// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import * as api from '../../api/beats'
import type { BeatDocument, BeatPresetCompilation } from '../../api/generated'
import { beatDocument } from './beatTestFixtures'
import { presetCatalog } from './beatPresetTestFixtures'
import BeatPresetBrowser from './BeatPresetBrowser.vue'

vi.mock('../../api/beats', () => ({ listBeatPresets: vi.fn(), compileBeatPreset: vi.fn() }))
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.listBeatPresets).mockResolvedValue(presetCatalog()) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 10; i++) await nextTick() }
async function mount(onCandidate?: (value: BeatDocument) => void) { const node = document.body.appendChild(document.createElement('div')); app = createApp({ render: () => h(BeatPresetBrowser, { onCandidate }) }).use(i18n); app.mount(node); await settle(); return node }
function button(node: HTMLElement, text: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === text); if (!found) throw new Error(`Missing ${text}`); return found }

it('loads on explicit opening and keeps unavailable, unreviewed candidates disabled', async () => {
  const node = await mount(); expect(api.listBeatPresets).not.toHaveBeenCalled()
  button(node, 'Regional preset catalog').click(); await settle()
  expect(node.querySelectorAll('article')).toHaveLength(24)
  expect(node.textContent).toContain('Unreviewed candidate')
  expect(button(node, 'Guzheng Dawn').disabled).toBe(true)
  const field = node.querySelector<HTMLInputElement>('input[type="search"]')
  if (!field) throw new Error('Missing search')
  field.value = 'Guzheng Dawn'; field.dispatchEvent(new Event('input')); await settle()
  expect(node.querySelectorAll('article')).toHaveLength(1)
  expect(api.compileBeatPreset).not.toHaveBeenCalled()
})
it('emits only an explicitly compiled candidate with a compatible kit', async () => {
  vi.mocked(api.listBeatPresets).mockResolvedValue(presetCatalog(true))
  vi.mocked(api.compileBeatPreset).mockImplementation(async request => ({ preset_id: request.preset_id, preset_sha256: 'c'.repeat(64), catalog_version: 'report-v1-provisional', review_status: 'unreviewed', warnings: ['candidate_arrangement_unreviewed'], document: { ...beatDocument(), id: request.document_id } }))
  const accept = vi.fn(), node = await mount(accept)
  button(node, 'Regional preset catalog').click(); await settle(); button(node, 'Guzheng Dawn').click(); await settle()
  expect(api.compileBeatPreset).not.toHaveBeenCalled()
  button(node, 'Create beat from this candidate').click(); await settle()
  expect(api.compileBeatPreset).toHaveBeenCalledWith(expect.objectContaining({ preset_id: 'guzheng_dawn', kit_id: 'test-kit', tonic_midi: 60 }), expect.any(AbortSignal))
  expect(accept).toHaveBeenCalledOnce()
})
it('aborts a late compile on teardown without creating a beat', async () => {
  vi.mocked(api.listBeatPresets).mockResolvedValue(presetCatalog(true))
  let finish: ((value: BeatPresetCompilation) => void) | undefined
  vi.mocked(api.compileBeatPreset).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const accept = vi.fn(), node = await mount(accept)
  button(node, 'Regional preset catalog').click(); await settle(); button(node, 'Guzheng Dawn').click(); await settle(); button(node, 'Create beat from this candidate').click(); await settle()
  const request = vi.mocked(api.compileBeatPreset).mock.calls[0]
  app?.unmount(); app = undefined
  expect(request?.[1]?.aborted).toBe(true)
  if (!request) throw new Error('Missing compile request')
  finish?.({ preset_id: request[0].preset_id, preset_sha256: 'c'.repeat(64), catalog_version: 'report-v1-provisional', review_status: 'unreviewed', warnings: ['candidate_arrangement_unreviewed'], document: beatDocument() }); await settle()
  expect(accept).not.toHaveBeenCalled()
})
