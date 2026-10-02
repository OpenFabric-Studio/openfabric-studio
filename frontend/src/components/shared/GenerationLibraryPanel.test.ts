// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import GenerationLibraryPanel from './GenerationLibraryPanel.vue'
import { i18n, setLocale, type LocaleCode } from '../../i18n'
import * as api from '../../api/generationLibrary'
import { emptyAceSettings, emptyYueSettings } from '../../composables/generationSnapshots'
import { pendingYueDraft, editingGenerationPreset } from '../../composables/generationDrafts'
import type { GenerationPreset, GenerationHistoryEntry } from '../../api/contracts'
vi.mock('../../api/generationLibrary', async original => ({ ...await original<typeof import('../../api/generationLibrary')>(), listHistory: vi.fn(), listPresets: vi.fn(), getPreset: vi.fn(), createPreset: vi.fn(), updatePreset: vi.fn(), duplicatePreset: vi.fn(), deletePreset: vi.fn(), importPresets: vi.fn() }))
const preset: GenerationPreset = { id: 'a'.repeat(32), name: 'Saved jazz', engine: 'yue2', settings: { ...emptyYueSettings(), lyrics: 'saved lyrics', style: 'jazz' }, revision: 1, created_at: '2026-10-01', updated_at: '2026-10-01', source_history_id: null }
const history: GenerationHistoryEntry = { id: 'b'.repeat(32), engine: 'yue2', title: 'Previous jazz', lyrics: 'saved lyrics', seed: 2, track_id: null, settings: preset.settings, created_at: '2026-10-01', reference_requires_reupload: false }
let app: App | undefined
beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); setLocale('en'); pendingYueDraft.value = null; editingGenerationPreset.value = null; vi.mocked(api.listHistory).mockResolvedValue({ data: [history], total: 1, retention_limit: 100 }); vi.mocked(api.listPresets).mockResolvedValue({ data: [preset], total: 1 }); vi.mocked(api.importPresets).mockResolvedValue({ data: [], imported: 0 }); vi.mocked(api.deletePreset).mockResolvedValue(undefined) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let index = 0; index < 15; index++) await nextTick() }
async function mount() { const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerationLibraryPanel, { engine: 'yue2', currentSettings: emptyYueSettings() }).use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }, { path: '/yue2', component: { render: () => null } }] })); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
it('shows friendly engine names in history and preset rows, including untitled history', async () => {
  vi.mocked(api.listHistory).mockResolvedValue({ data: [{ ...history, id: 'c'.repeat(32), engine: 'ace_step', title: '', settings: emptyAceSettings() }, history], total: 2, retention_limit: 100 })
  vi.mocked(api.listPresets).mockResolvedValue({ data: [{ ...preset, id: 'd'.repeat(32), engine: 'ace_step', settings: emptyAceSettings() }, preset], total: 2 })
  const container = await mount()
  expect(container.querySelector('article p')?.textContent?.trim()).toMatch(/^ACE-Step ACE-Step/)
  for (const engineName of ['ACE-Step', 'YuE']) expect(container.textContent).toContain(engineName)
  for (const engineCode of ['ace_step', 'yue2']) expect(container.textContent).not.toContain(engineCode)
  button(container, 'Saved presets').click(); await settle()
  for (const engineName of ['ACE-Step', 'YuE']) expect(container.textContent).toContain(engineName)
  for (const engineCode of ['ace_step', 'yue2']) expect(container.textContent).not.toContain(engineCode)
})
it.each<{ locale: LocaleCode; historyLabel: string; presetsLabel: string; historySearch: string; presetSearch: string }>([
  { locale: 'en', historyLabel: 'History', presetsLabel: 'Saved presets', historySearch: 'Search lyrics, style or title', presetSearch: 'Search saved preset names' },
  { locale: 'ru', historyLabel: 'История', presetsLabel: 'Сохранённые пресеты', historySearch: 'Поиск по тексту, стилю или названию', presetSearch: 'Поиск по именам сохранённых пресетов' },
])('describes the active search scope in $locale', async ({ locale, historyLabel, presetsLabel, historySearch, presetSearch }) => {
  setLocale(locale)
  const container = await mount()
  const search = container.querySelector('input[type="search"]')
  expect(search?.getAttribute('placeholder')).toBe(historySearch)
  button(container, presetsLabel).click(); await settle()
  expect(search?.getAttribute('placeholder')).toBe(presetSearch)
  expect(search?.getAttribute('aria-label')).toBe(presetSearch)
  button(container, historyLabel).click(); await settle()
  expect(search?.getAttribute('placeholder')).toBe(historySearch)
  expect(search?.getAttribute('aria-label')).toBe(historySearch)
})
it('reuses durable lyrics and settings even after the audio track has been deleted', async () => {
  const container = await mount(); button(container, 'Reuse settings').click(); await settle()
  expect(pendingYueDraft.value?.lyrics).toBe('saved lyrics'); expect(container.textContent).toContain('no longer has an audio track')
})
it('requires explicit confirmation before deleting a permanent preset', async () => {
  const container = await mount(); button(container, 'Saved presets').click(); await settle(); button(container, 'Delete').click(); await settle()
  expect(api.deletePreset).not.toHaveBeenCalled(); expect(container.textContent).toContain('Audio and history are unaffected')
  button(container, 'Delete preset').click(); await settle(); expect(api.deletePreset).toHaveBeenCalledWith(preset.id, 1, expect.any(AbortSignal))
})
it('opens the normal generator form for preset editing instead of mutating saved settings', async () => {
  const container = await mount(); button(container, 'Saved presets').click(); await settle(); button(container, 'Edit in generator').click(); await settle()
  expect(editingGenerationPreset.value?.id).toBe(preset.id); expect(pendingYueDraft.value?.style).toBe('jazz'); expect(api.updatePreset).not.toHaveBeenCalled()
})
it('imports validated browser presets while retaining the original local data', async () => {
  const raw = JSON.stringify([{ name: 'Legacy', cot: 'off', precision: 'q8_0', lyrics: 'legacy lyrics' }]); localStorage.setItem('yue2_presets', raw)
  await mount(); expect(api.importPresets).toHaveBeenCalledWith(expect.objectContaining({ presets: [expect.objectContaining({ legacy_key: expect.stringMatching(/^yue2_presets:Legacy:/) })] }), expect.any(AbortSignal)); expect(localStorage.getItem('yue2_presets')).toBe(raw)
})
it('reaches preset101 and resets pagination when switching tabs', async () => {
  const firstPage = Array.from({ length: 100 }, (_value, index) => ({ ...preset, id: index.toString(16).padStart(32, '0'), name: `Preset ${index + 1}` }))
  vi.mocked(api.listPresets).mockImplementation(async filter => ({ data: filter?.offset === 100 ? [{ ...preset, name: 'Preset 101' }] : firstPage, total: 101 }))
  const container = await mount(); button(container, 'Saved presets').click(); await settle(); button(container, 'Next').click(); await settle()
  expect(container.textContent).toContain('Preset 101'); expect(container.textContent).not.toContain('Preset 99')
  expect(api.listPresets).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 100 }), expect.any(AbortSignal))
  button(container, 'History').click(); await settle(); expect(api.listHistory).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 }), expect.any(AbortSignal))
})
it('reloads an edited preset revision beyond the current page without replacing the form draft', async () => {
  editingGenerationPreset.value = preset; vi.mocked(api.listPresets).mockResolvedValue({ data: [], total: 101 })
  vi.mocked(api.getPreset).mockResolvedValue({ ...preset, name: 'Renamed elsewhere', revision: 2 })
  vi.mocked(api.updatePreset).mockResolvedValue({ ...preset, name: 'Renamed elsewhere', revision: 3 })
  const container = await mount(); button(container, 'Reload').click(); await settle()
  expect(api.getPreset).toHaveBeenCalledWith(preset.id, expect.any(AbortSignal)); expect(pendingYueDraft.value).toBeNull()
  button(container, 'Update “Renamed elsewhere” from current form').click(); await settle()
  expect(api.updatePreset).toHaveBeenCalledWith(preset.id, expect.objectContaining({ revision: 2, settings: emptyYueSettings() }), expect.any(AbortSignal))
})
it('uses completed lyrics and the actual seed for both reuse and saving a history preset', async () => {
  vi.mocked(api.listHistory).mockResolvedValue({ data: [{ ...history, lyrics: 'actual result', seed: 88, settings: { ...emptyYueSettings(), lyrics: 'original input', randomSeed: true, seed: 11 } }], total: 1, retention_limit: 100 })
  vi.mocked(api.createPreset).mockResolvedValue(preset)
  const container = await mount(); button(container, 'Reuse settings').click(); await settle(); expect(pendingYueDraft.value).toMatchObject({ lyrics: 'actual result', seed: 88, randomSeed: false })
  button(container, 'Save as preset').click(); await settle(); button(container, 'Save').click(); await settle()
  expect(api.createPreset).toHaveBeenCalledWith(expect.objectContaining({ settings: expect.objectContaining({ lyrics: 'actual result', seed: 88, randomSeed: false }) }), expect.any(AbortSignal))
})
