// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import GenerateForm from './GenerateForm.vue'
import { i18n, setLocale } from '../../i18n'
import type { JsonObject } from '../../api/contracts'
import { parseAceGenerationSettings } from '../../api/contracts'
import { emptyAceSettings } from '../../composables/generationSnapshots'
import { pendingAceDraft } from '../../composables/generationDrafts'
import * as aceApi from '../../api/aceStep'
import { ApiError } from '../../api/http'
const state = reactive<{ pendingParamsInsert: JsonObject | null }>({ pendingParamsInsert: null })
const submit = vi.fn()
vi.mock('../../stores/aceStep', () => ({ useAceStepStore: () => ({ get pendingParamsInsert() { return state.pendingParamsInsert }, inventory: null, submit, clearPendingParamsInsert: () => { state.pendingParamsInsert = null } }) }))
vi.mock('../../components/shared/VoiceSelect.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
vi.mock('../../api/aceStep', async original => ({ ...await original<typeof import('../../api/aceStep')>(), loraLoad: vi.fn(async () => {}), loraUnload: vi.fn(async () => {}), loraScale: vi.fn(async () => {}) }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear(); state.pendingParamsInsert = null; pendingAceDraft.value = null; submit.mockReset() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount() { setLocale('en'); const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerateForm).use(i18n); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
it.each([0.4, 2])('restores saved ACE strength %s only into supported live controls without changing the saved snapshot', async scale => {
  const saved = parseAceGenerationSettings({ ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz', loraScale: scale })
  const original = JSON.stringify(saved)
  pendingAceDraft.value = saved
  const container = await mount()
  button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledWith(expect.any(Object), null, 'Jazz', null, expect.objectContaining({ loraScale: Math.min(scale, 1) }))
  expect(JSON.stringify(saved)).toBe(original)
  expect(container.textContent?.includes('ACE-Step supports LoRA strength up to 1')).toBe(scale > 1)
})

it('associates generator selects, ranges and textareas with their existing translated labels', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  pendingAceDraft.value = { ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz', durationAuto: false, useRefAudio: true, taskType: 'cover' }
  const container = await mount()
  const adapter = [...container.querySelectorAll('select')].find(select => select.querySelector('option[value="/fixture"]'))
  if (!adapter) throw new Error('Missing adapter selector')
  adapter.value = '/fixture'; adapter.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  for (const field of container.querySelectorAll('select, input[type="range"], textarea')) {
    if (!(field instanceof HTMLSelectElement || field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) throw new Error('Unexpected control')
    expect([...field.labels ?? []].some(label => !!label.textContent?.trim()), field.outerHTML).toBe(true)
  }
  const strength = container.querySelector('#ace-lora-strength')
  expect(strength?.getAttribute('max')).toBe('1')
})

it('shows a translated LoRA failure instead of a vendor error or an internal code', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  vi.mocked(aceApi.loraScale).mockRejectedValueOnce(new ApiError('native_operation_failed', 400))
  const container = await mount()
  const adapter = [...container.querySelectorAll('select')].find(select => select.querySelector('option[value="/fixture"]'))
  if (!adapter) throw new Error('Missing adapter selector')
  adapter.value = '/fixture'; adapter.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(container.textContent).toContain('Could not apply these LoRA settings')
  expect(container.textContent).not.toContain('native_operation_failed')
})
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
