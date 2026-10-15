// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import GenerateForm from './GenerateForm.vue'
import { i18n, setLocale } from '../../i18n'
import type { JsonObject } from '../../api/contracts'
import { ApiError } from '../../api/http'
const state = reactive<{ pendingParamsInsert: JsonObject | null; pendingAbcInsert: string | null }>({ pendingParamsInsert: null, pendingAbcInsert: null })
const generateBatch = vi.fn()
vi.mock('../../stores/yue2', () => ({ useYue2Store: () => ({ ...state, get pendingParamsInsert() { return state.pendingParamsInsert }, generateBatch, clearPendingParamsInsert: () => { state.pendingParamsInsert = null }, clearPendingAbcInsert: () => { state.pendingAbcInsert = null } }) }))
vi.mock('../../components/shared/VoiceSelect.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
vi.mock('../../api/musicLora', () => ({ listMusicAdapters: vi.fn(async () => ({ adapters: [{ id: 'a'.repeat(32), engine: 'yue2', target: 'ar', name: 'AR', sha256: 'c'.repeat(64), created_at: 'now', source_job_id: null }, { id: 'b'.repeat(32), engine: 'yue2', target: 'nar', name: 'NAR', sha256: 'd'.repeat(64), created_at: 'now', source_job_id: null }], history_incomplete: false })) }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear(); state.pendingParamsInsert = null; generateBatch.mockReset() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount() { setLocale('en'); const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: GenerateForm }] }); await router.push('/'); const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerateForm).use(i18n).use(router); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
it('uses translated song title labels, placeholder and help text', async () => {
  const container = await mount()
  expect(container.querySelector('label[for="yue-song-title"]')?.textContent).toBe('Song title')
  expect(container.querySelector('#yue-song-title')?.getAttribute('aria-label')).toBe('Song title')
  expect(container.querySelector('#yue-song-title')?.getAttribute('placeholder')).toBe('Name this song')
  expect(container.textContent).toContain('Saved separately from the style tags.')
})
it('associates generation selects, sampling inputs and textareas with translated labels', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'melody' }; await settle()
  for (const field of container.querySelectorAll('select:not([name]), input[type="number"], textarea')) {
    if (!(field instanceof HTMLSelectElement || field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) throw new Error('Unexpected control')
    expect([...field.labels ?? []].some(label => !!label.textContent?.trim()), field.outerHTML).toBe(true)
  }
})
it('copying sparse parameters resets omitted steps, CFG and sampling values', async () => {
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'old', style: 'rock', cfg_scale: 4, num_inference_steps: 32, semantic_temperature: 0.8, abc_temperature: 0.7 }; await settle()
  state.pendingParamsInsert = { lyrics: 'new lyrics', style: 'jazz' }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ lyrics: 'new lyrics', style: 'jazz', options: { style: 'jazz', cot: 'off', num_inference_steps: 8 } }))
})
it('preserves deliberately absent steps and zero sampling while restoring a complete preset', async () => {
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', num_inference_steps: null, cfg_scale: null, semantic_temperature: 0 }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ options: { style: 'jazz', cot: 'off', semantic_temperature: 0 } }))
})
it('applies reviewed reference text without resetting the existing generator settings', async () => {
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'old lyrics', style: 'jazz', seed: 55, cot: 'off', num_inference_steps: 12 }; await settle()
  const { applyReferenceText } = await import('../../composables/generationDrafts')
  applyReferenceText('yue2', 'abc', 'X:1\nK:C\nC D E', 'a'.repeat(32)); await settle()
  applyReferenceText('yue2', 'lyrics', 'reviewed lyrics', 'a'.repeat(32)); await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ lyrics: 'reviewed lyrics', style: 'jazz', baseSeed: 55, cot: 'melody', options: { style: 'jazz', cot: 'melody', abc: 'X:1\nK:C\nC D E', num_inference_steps: 12 }, settings: expect.objectContaining({ referenceImportId: 'a'.repeat(32) }) }))
})
it('clears unrelated reference provenance when copying another track parameters', async () => {
  const container = await mount(); const { applyReferenceText } = await import('../../composables/generationDrafts')
  applyReferenceText('yue2', 'lyrics', 'reference lyrics', 'a'.repeat(32)); await settle()
  state.pendingParamsInsert = { lyrics: 'different lyrics', style: 'jazz' }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ settings: expect.objectContaining({ referenceImportId: null }) }))
})
it('restores independent managed adapters from copied parameters and resets them on sparse copies', async () => {
  const container = await mount()
  const ar = { id: 'a'.repeat(32), strength: 0.5 }, nar = { id: 'b'.repeat(32), strength: 0 }
  state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', ar_adapter: ar, nar_adapter: nar }; await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: ar, narAdapter: nar, settings: expect.objectContaining({ arAdapter: ar, narAdapter: nar }) }))
  state.pendingParamsInsert = { lyrics: 'plain', style: 'rock' }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: null, narAdapter: null, settings: expect.objectContaining({ arAdapter: null, narAdapter: null }) }))
})
it('rejects native paths and invalid strengths copied from track parameters', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', ar_adapter: { id: '/private/adapter.safetensors', strength: 1 }, nar_adapter: { id: 'b'.repeat(32), strength: 3 } }; await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ arAdapter: null, narAdapter: null }))
})
it('reapplies both adapters from a complete generation preset', async () => {
  const container = await mount()
  const { emptyYueSettings } = await import('../../composables/generationSnapshots')
  const { pendingYueDraft } = await import('../../composables/generationDrafts')
  const ar = { id: 'a'.repeat(32), strength: 0.25 }, nar = { id: 'b'.repeat(32), strength: 1.25 }
  pendingYueDraft.value = { ...emptyYueSettings(), lyrics: 'preset lyrics', style: 'folk', arAdapter: ar, narAdapter: nar }
  await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ lyrics: 'preset lyrics', arAdapter: ar, narAdapter: nar, settings: expect.objectContaining({ arAdapter: ar, narAdapter: nar }) }))
})

it('blocks YuE generation while an adapter strength is invalid and resumes with the corrected value', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', ar_adapter: { id: 'a'.repeat(32), strength: 1 } }; await settle()
  const selector = container.querySelector('select[name="adapter-ar"]')
  const strength = selector?.closest('fieldset')?.querySelector('input')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing AR strength')
  strength.value = '3'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  const create = button(container, '✨ Create music')
  expect(create.disabled).toBe(true)
  create.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
  expect(generateBatch).not.toHaveBeenCalled()
  strength.value = '0.4'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(create.disabled).toBe(false)
  create.click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ arAdapter: { id: 'a'.repeat(32), strength: 0.4 } }))
})

it('explains an unsupported runtime adapter format on direct submission without raw errors', async () => {
  generateBatch.mockRejectedValue(new ApiError('music_lora_runtime_unsupported', 409))
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz' }; await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(container.textContent).toContain('Update the native engine before using this adapter format')
  expect(container.textContent).not.toContain('music_lora_runtime_unsupported')
})
