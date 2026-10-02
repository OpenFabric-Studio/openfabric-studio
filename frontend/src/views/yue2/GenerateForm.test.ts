// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import GenerateForm from './GenerateForm.vue'
import { i18n, setLocale } from '../../i18n'
import type { JsonObject } from '../../api/contracts'
const state = reactive<{ pendingParamsInsert: JsonObject | null; pendingAbcInsert: string | null }>({ pendingParamsInsert: null, pendingAbcInsert: null })
const generateBatch = vi.fn()
vi.mock('../../stores/yue2', () => ({ useYue2Store: () => ({ ...state, get pendingParamsInsert() { return state.pendingParamsInsert }, generateBatch, clearPendingParamsInsert: () => { state.pendingParamsInsert = null }, clearPendingAbcInsert: () => { state.pendingAbcInsert = null } }) }))
vi.mock('../../components/shared/VoiceSelect.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear(); state.pendingParamsInsert = null; generateBatch.mockReset() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount() { setLocale('en'); const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerateForm).use(i18n); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
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
