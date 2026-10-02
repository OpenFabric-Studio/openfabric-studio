// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import GenerateForm from './GenerateForm.vue'
import { i18n, setLocale } from '../../i18n'
import type { JsonObject } from '../../api/contracts'
const state = reactive<{ pendingParamsInsert: JsonObject | null }>({ pendingParamsInsert: null })
const submit = vi.fn()
vi.mock('../../stores/aceStep', () => ({ useAceStepStore: () => ({ get pendingParamsInsert() { return state.pendingParamsInsert }, inventory: null, submit, clearPendingParamsInsert: () => { state.pendingParamsInsert = null } }) }))
vi.mock('../../components/shared/VoiceSelect.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear(); state.pendingParamsInsert = null; submit.mockReset() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount() { setLocale('en'); const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerateForm).use(i18n); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
it('copying sparse parameters clears old seed, inference and musical constraints', async () => {
  const container = await mount(); state.pendingParamsInsert = { prompt: 'rock', lyrics: 'old', inference_steps: 20, guidance_scale: 4, seed: 123, bpm: 110, key_scale: 'C major', audio_format: 'wav', audio_duration: 60 }; await settle()
  state.pendingParamsInsert = { prompt: 'jazz', lyrics: '' }; await settle(); button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledWith({ prompt: 'jazz', lyrics: '', audio_duration: -1, batch_size: 1, audio_format: 'mp3', use_cot_caption: true, use_random_seed: true }, null, 'jazz', null, expect.objectContaining({ inferenceSteps: null, seed: null }))
})
it('requires a missing saved style reference to be reselected or explicitly removed', async () => {
  const container = await mount()
  const { pendingAceDraft } = await import('../../composables/generationDrafts')
  const { emptyAceSettings } = await import('../../composables/generationSnapshots')
  pendingAceDraft.value = { ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz', styleReferenceRequiresReupload: true, styleReferenceName: 'style.wav' }
  await settle(); button(container, 'Generate').click(); await settle()
  expect(submit).not.toHaveBeenCalled()
  button(container, 'Remove saved style reference').click(); await settle(); button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledTimes(1)
})
it('applies reviewed lyrics to custom mode while retaining current style and seed', async () => {
  const container = await mount(); state.pendingParamsInsert = { prompt: 'jazz', lyrics: 'old', seed: 55 }; await settle()
  const { applyReferenceText } = await import('../../composables/generationDrafts')
  applyReferenceText('ace_step', 'lyrics', 'reviewed lyrics', 'b'.repeat(32)); await settle(); button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ prompt: 'jazz', lyrics: 'reviewed lyrics', seed: 55 }), null, 'jazz', null, expect.objectContaining({ referenceImportId: 'b'.repeat(32), customLyrics: 'reviewed lyrics' }))
})
it('clears unrelated style reference and caption settings when copying another track parameters', async () => {
  const container = await mount()
  const style = container.querySelector<HTMLInputElement>('[aria-label="Style / timbre reference audio"]'); if (!style) throw new Error('Style input missing')
  Object.defineProperty(style, 'files', { value: [new File(['old'], 'old-style.wav', { type: 'audio/wav' })] }); style.dispatchEvent(new Event('change')); await settle()
  const { applyReferenceText } = await import('../../composables/generationDrafts'); applyReferenceText('ace_step', 'lyrics', 'reference lyrics', 'a'.repeat(32)); await settle()
  state.pendingParamsInsert = { prompt: 'different jazz', lyrics: 'different lyrics', use_cot_caption: false }; await settle(); button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ use_cot_caption: false }), null, 'different jazz', null, expect.objectContaining({ referenceImportId: null, styleReferenceName: null, styleReferenceRequiresReupload: false }))
})
