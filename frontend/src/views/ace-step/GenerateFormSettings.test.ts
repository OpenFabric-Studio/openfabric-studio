// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import GenerateForm from './GenerateForm.vue'
import { i18n, setLocale } from '../../i18n'
import type { JsonObject } from '../../api/contracts'
import { parseAceGenerationSettings } from '../../api/contracts'
import type { AceGenerationSettings } from '../../api/contracts'
import { emptyAceSettings } from '../../composables/generationSnapshots'
import { pendingAceDraft } from '../../composables/generationDrafts'
import * as aceApi from '../../api/aceStep'
import { ApiError } from '../../api/http'
import type { AceLoraStatus } from '../../api/generated'
import type { ModelInventory } from '../../api/aceStep'
let nativeLora: AceLoraStatus = { lora_loaded: false, use_lora: false, lora_scale: 1, active_adapter: null, adapters: [] }
const state = reactive<{ pendingParamsInsert: JsonObject | null; inventory: ModelInventory | null; inventoryLoading: boolean; inventoryError: string }>({ pendingParamsInsert: null, inventory: null, inventoryLoading: false, inventoryError: '' })
const submit = vi.fn()
const loadInventory = vi.fn<() => Promise<void>>()
const capturedSettings: AceGenerationSettings[] = []
vi.mock('../../stores/aceStep', () => ({ useAceStepStore: () => ({ get pendingParamsInsert() { return state.pendingParamsInsert }, get inventory() { return state.inventory }, get inventoryLoading() { return state.inventoryLoading }, get inventoryError() { return state.inventoryError }, loadInventory, submit, clearPendingParamsInsert: () => { state.pendingParamsInsert = null } }) }))
vi.mock('../../components/shared/VoiceSelect.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
vi.mock('../../components/shared/GenerationLibraryButton.vue', () => ({ default: defineComponent({ props: ['settings'], setup(props) { return () => h('button', { onClick: () => capturedSettings.push(parseAceGenerationSettings(props.settings)) }, 'Capture current settings') } }) }))
vi.mock('../../api/aceStep', async original => ({ ...await original<typeof import('../../api/aceStep')>(), loraStatus: vi.fn(), loraLoad: vi.fn(), loraUnload: vi.fn(), loraScale: vi.fn(), loraToggle: vi.fn() }))
let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks()
  capturedSettings.length = 0
  loadInventory.mockResolvedValue(undefined)
  nativeLora = { lora_loaded: false, use_lora: false, lora_scale: 1, active_adapter: null, adapters: [] }
  vi.mocked(aceApi.loraStatus).mockImplementation(async () => ({ ...nativeLora }))
  vi.mocked(aceApi.loraLoad).mockImplementation(async (_path, name) => { nativeLora = { ...nativeLora, lora_loaded: true, use_lora: true, active_adapter: name ?? 'Native adapter', adapters: [name ?? 'Native adapter'] } })
  vi.mocked(aceApi.loraUnload).mockImplementation(async () => { nativeLora = { ...nativeLora, lora_loaded: false, use_lora: false, active_adapter: null, adapters: [] } })
  vi.mocked(aceApi.loraScale).mockImplementation(async scale => { nativeLora = { ...nativeLora, lora_scale: scale } })
  vi.mocked(aceApi.loraToggle).mockImplementation(async enabled => { nativeLora = { ...nativeLora, use_lora: enabled } })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear(); state.pendingParamsInsert = null; state.inventory = null; state.inventoryLoading = false; state.inventoryError = ''; pendingAceDraft.value = null; submit.mockReset(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 12; index++) await nextTick() }
async function mount() { setLocale('en'); const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerateForm).use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] })); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
function adapter(container: HTMLElement) { const node = container.querySelector('#ace-lora'); if (!(node instanceof HTMLSelectElement)) throw new Error('Missing adapter selector'); return node }
async function choose(container: HTMLElement, value: string) { const node = adapter(container); node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); await settle() }
function jazzDraft() { pendingAceDraft.value = { ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz' } }

it('blocks generation when native LoRA status is unknown and recovers after a successful recheck', async () => {
  vi.mocked(aceApi.loraStatus).mockRejectedValueOnce(new ApiError('ace_lora_status_unavailable', 502))
  jazzDraft(); const container = await mount()
  expect(button(container, 'Generate').disabled).toBe(true)
  expect(container.textContent).toContain('Could not check the active LoRA')
  button(container, 'Retry LoRA settings').click(); await settle()
  expect(button(container, 'Generate').disabled).toBe(false)
})

it('shows actual loaded state and explicitly reuses it without loading a registered path', async () => {
  nativeLora = { lora_loaded: true, use_lora: true, lora_scale: 0.75, active_adapter: 'Native name', adapters: ['Native name'] }
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Native name', path: '/unrelated' }]))
  jazzDraft(); const container = await mount()
  expect(container.textContent).toContain('Native name')
  expect(container.textContent).toContain('0.75')
  expect(button(container, 'Generate').disabled).toBe(true)
  await choose(container, 'current')
  expect(aceApi.loraLoad).not.toHaveBeenCalled()
  expect(aceApi.loraScale).toHaveBeenCalledExactlyOnceWith(0.75)
  expect(button(container, 'Generate').disabled).toBe(false)
  button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledWith(expect.any(Object), null, 'Jazz', null, expect.objectContaining({ loraName: 'Native name', loraScale: 0.75, loraRequiresReselection: true }))
})
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
  const adapter = [...container.querySelectorAll('select')].find(select => select.querySelector('option[value="path:/fixture"]'))
  if (!adapter) throw new Error('Missing adapter selector')
  adapter.value = 'path:/fixture'; adapter.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  for (const field of container.querySelectorAll('select, input[type="range"], textarea')) {
    if (!(field instanceof HTMLSelectElement || field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) throw new Error('Unexpected control')
    expect([...field.labels ?? []].some(label => !!label.textContent?.trim()), field.outerHTML).toBe(true)
  }
  const strength = container.querySelector('#ace-lora-strength')
  expect(strength?.getAttribute('max')).toBe('1')
})

it('shows a translated LoRA failure instead of a vendor error or an internal code', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  const container = await mount()
  const adapter = [...container.querySelectorAll('select')].find(select => select.querySelector('option[value="path:/fixture"]'))
  if (!adapter) throw new Error('Missing adapter selector')
  adapter.value = 'path:/fixture'; adapter.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  vi.mocked(aceApi.loraScale).mockRejectedValueOnce(new ApiError('native_operation_failed', 400))
  const strength = container.querySelector('#ace-lora-strength')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing strength')
  strength.value = '0.5'; strength.dispatchEvent(new Event('input', { bubbles: true })); await vi.advanceTimersByTimeAsync(300); await settle()
  expect(container.textContent).toContain('Could not apply these LoRA settings')
  expect(container.textContent).not.toContain('native_operation_failed')
})

it('rejects a successful load acknowledgement if another adapter is actually active', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Requested', path: '/fixture' }]))
  vi.mocked(aceApi.loraLoad).mockImplementationOnce(async () => { nativeLora = { lora_loaded: true, use_lora: true, lora_scale: 0.5, active_adapter: 'Other adapter', adapters: ['Other adapter'] } })
  jazzDraft(); const container = await mount(); await choose(container, 'path:/fixture')
  expect(aceApi.loraScale).not.toHaveBeenCalled()
  expect(button(container, 'Generate').disabled).toBe(true)
  expect(container.textContent).toContain('no longer matches this selection')
  button(container, 'Generate').click(); await settle()
  expect(submit).not.toHaveBeenCalled()
})

it('stops duplicate loads without changing or scaling the previously confirmed adapter', async () => {
  nativeLora = { lora_loaded: true, use_lora: true, lora_scale: 0.75, active_adapter: 'Existing', adapters: ['Existing'] }
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Replacement', path: '/fixture' }]))
  jazzDraft(); const container = await mount(); await choose(container, 'current')
  vi.mocked(aceApi.loraScale).mockClear()
  vi.mocked(aceApi.loraLoad).mockRejectedValue(new ApiError('ace_lora_already_loaded', 400))
  await choose(container, 'path:/fixture')
  expect(aceApi.loraLoad).toHaveBeenCalledExactlyOnceWith('/fixture', 'Replacement')
  expect(aceApi.loraUnload).not.toHaveBeenCalled()
  const strength = container.querySelector('#ace-lora-strength')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing strength')
  expect(strength.disabled).toBe(true)
  strength.value = '0.4'; strength.dispatchEvent(new Event('input', { bubbles: true })); await vi.advanceTimersByTimeAsync(300)
  expect(aceApi.loraScale).not.toHaveBeenCalled()
  expect(button(container, 'Generate').disabled).toBe(true)
  expect(container.textContent).toContain('An adapter is already loaded')
  await choose(container, 'current')
  expect(button(container, 'Generate').disabled).toBe(false)
})

it.each(['ace_lora_path_missing', 'ace_lora_invalid_adapter'])('tries the nested adapter layout only for %s', async code => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  vi.mocked(aceApi.loraLoad).mockRejectedValueOnce(new ApiError(code, 400))
  const container = await mount(); await choose(container, 'path:/fixture')
  expect(vi.mocked(aceApi.loraLoad).mock.calls).toEqual([['/fixture', 'Fixture'], ['/fixture/adapter', 'Fixture']])
  expect(button(container, 'Generate').disabled).toBe(false)
})

it.each(['ace_lora_busy', 'ace_lora_quantized_unsupported', 'ace_lora_operation_failed'])('does not retry nested paths for %s', async code => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  vi.mocked(aceApi.loraLoad).mockRejectedValue(new ApiError(code, 400))
  const container = await mount(); await choose(container, 'path:/fixture')
  expect(aceApi.loraLoad).toHaveBeenCalledTimes(1)
  expect(aceApi.loraScale).not.toHaveBeenCalled()
  expect(button(container, 'Generate').disabled).toBe(true)
})

it('debounces strength edits and clears an old strength failure only after confirmed recovery', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  jazzDraft(); const container = await mount(); await choose(container, 'path:/fixture')
  vi.mocked(aceApi.loraScale).mockClear()
  const strength = container.querySelector('#ace-lora-strength')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing strength')
  vi.mocked(aceApi.loraScale).mockRejectedValueOnce(new ApiError('ace_lora_operation_failed', 400))
  strength.value = '0.4'; strength.dispatchEvent(new Event('input', { bubbles: true })); await vi.advanceTimersByTimeAsync(300); await settle()
  expect(container.textContent).toContain('Could not apply these LoRA settings')
  expect(button(container, 'Generate').disabled).toBe(true)
  expect(strength.disabled).toBe(false)
  strength.value = '0.6'; strength.dispatchEvent(new Event('input', { bubbles: true }))
  strength.value = '0.75'; strength.dispatchEvent(new Event('input', { bubbles: true })); await vi.advanceTimersByTimeAsync(300); await settle()
  expect(vi.mocked(aceApi.loraScale).mock.calls).toEqual([[0.4], [0.75]])
  expect(container.textContent).not.toContain('Could not apply these LoRA settings')
  button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledWith(expect.any(Object), null, 'Jazz', null, expect.objectContaining({ loraName: 'Fixture', loraScale: 0.75 }))
})

it('serializes adapter controls and stops follow-up calls after unmount during a load', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  let finish: (() => void) | undefined
  vi.mocked(aceApi.loraLoad).mockReturnValueOnce(new Promise<void>(resolve => { finish = resolve }))
  const container = await mount(); await choose(container, 'path:/fixture')
  expect(adapter(container).disabled).toBe(true)
  await choose(container, 'none')
  expect(aceApi.loraUnload).not.toHaveBeenCalled()
  app?.unmount(); app = undefined; finish?.(); await settle(); await vi.advanceTimersByTimeAsync(1000)
  expect(aceApi.loraStatus).toHaveBeenCalledTimes(1)
  expect(aceApi.loraScale).not.toHaveBeenCalled()
  expect(loadInventory).not.toHaveBeenCalled()
})

it('checks status again before generation and blocks an externally changed adapter with a visible error', async () => {
  jazzDraft(); const container = await mount()
  nativeLora = { lora_loaded: true, use_lora: true, lora_scale: 0.75, active_adapter: 'External adapter', adapters: ['External adapter'] }
  button(container, 'Generate').click(); await settle()
  expect(submit).not.toHaveBeenCalled()
  expect(aceApi.loraStatus).toHaveBeenCalledTimes(2)
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('no longer matches this selection')
  expect(container.querySelector('[role="alert"]')?.closest('details')).toBeNull()
})

it('preserves saved adapter reselection and explicitly unloads before removing it', async () => {
  pendingAceDraft.value = { ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz', loraRequiresReselection: true, loraName: 'Saved adapter', loraScale: 0.4 }
  const container = await mount()
  expect(button(container, 'Generate').disabled).toBe(true)
  const remove = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === 'Continue without this LoRA')
  if (!remove) throw new Error('Missing saved adapter removal')
  remove.click(); await settle()
  expect(aceApi.loraUnload).toHaveBeenCalledTimes(1)
  button(container, 'Generate').click(); await settle()
  expect(submit).toHaveBeenCalledWith(expect.any(Object), null, 'Jazz', null, expect.objectContaining({ loraName: null, loraRequiresReselection: false }))
})

it('does not clear a failed strength mutation merely because reported status matches', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  jazzDraft(); const container = await mount(); await choose(container, 'path:/fixture')
  vi.mocked(aceApi.loraScale).mockClear()
  vi.mocked(aceApi.loraScale).mockImplementationOnce(async scale => { nativeLora = { ...nativeLora, lora_scale: scale }; throw new ApiError('ace_lora_operation_failed', 502) })
  const strength = container.querySelector('#ace-lora-strength')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing strength')
  strength.value = '0.5'; strength.dispatchEvent(new Event('input', { bubbles: true })); await vi.advanceTimersByTimeAsync(300); await settle()
  button(container, 'Recheck LoRA status').click(); await settle()
  expect(button(container, 'Generate').disabled).toBe(true)
  expect(container.textContent).toContain('Could not apply these LoRA settings')
  button(container, 'Retry LoRA settings').click(); await settle()
  expect(aceApi.loraScale).toHaveBeenCalledTimes(2)
  expect(button(container, 'Generate').disabled).toBe(false)
})

it('submits one captured request and settings snapshot when fields change during status verification', async () => {
  jazzDraft(); const container = await mount()
  let finish: ((value: AceLoraStatus) => void) | undefined
  vi.mocked(aceApi.loraStatus).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  button(container, 'Generate').click(); await settle()
  const prompt = container.querySelector('textarea')
  if (!(prompt instanceof HTMLTextAreaElement)) throw new Error('Missing prompt')
  prompt.value = 'Changed after click'; prompt.dispatchEvent(new Event('input', { bubbles: true }))
  const style = container.querySelector<HTMLInputElement>('[aria-label="Style / timbre reference audio"]')
  if (!style) throw new Error('Missing style reference')
  Object.defineProperty(style, 'files', { value: [new File(['new'], 'new-style.wav', { type: 'audio/wav' })] }); style.dispatchEvent(new Event('change')); await settle()
  finish?.({ ...nativeLora }); await settle()
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ prompt: 'Jazz' }), null, 'Jazz', null, expect.objectContaining({ customPrompt: 'Jazz', customLyrics: '', styleReferenceName: null }))
})

it('shows a safe final server LoRA guard failure after the fresh status check succeeds', async () => {
  jazzDraft(); submit.mockRejectedValueOnce(new ApiError('ace_lora_selection_mismatch', 409))
  const container = await mount(); button(container, 'Generate').click(); await settle()
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('no longer matches this selection')
  expect(container.textContent).not.toContain('ace_lora_selection_mismatch')
  expect(button(container, 'Generate').disabled).toBe(true)
  button(container, 'Retry LoRA settings').click(); await settle()
  expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(button(container, 'Generate').disabled).toBe(false)
})

it('blocks a saved secondary model while a primary-model adapter is active without silently replacing the choice', async () => {
  state.inventory = { models: [{ name: 'primary', is_default: true, is_loaded: true, supported_task_types: [] }, { name: 'secondary', is_default: false, is_loaded: false, supported_task_types: [] }], default_model: 'primary', lm_models: [], loaded_lm_model: null, llm_initialized: false }
  nativeLora = { lora_loaded: true, use_lora: true, lora_scale: 0.75, active_adapter: 'Existing', adapters: ['Existing'] }
  pendingAceDraft.value = { ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz', selectedModel: 'secondary', loraRequiresReselection: true, loraName: 'Existing', loraScale: 0.75 }
  const container = await mount(); await choose(container, 'current')
  const models = container.querySelector('#ace-model')
  if (!(models instanceof HTMLSelectElement)) throw new Error('Missing base model')
  expect(models.value).toBe('secondary')
  expect(models.querySelector('option[value="secondary"]')).toHaveProperty('disabled', true)
  expect(button(container, 'Generate').disabled).toBe(true)
  expect(container.textContent).toContain('Choose No LoRA before switching the base model')
  models.value = 'primary'; models.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(button(container, 'Generate').disabled).toBe(false)
  await choose(container, 'none')
  expect(models.querySelector('option[value="secondary"]')).toHaveProperty('disabled', false)
})

it('retains a saved adapter identity when saving the draft again before reselection', async () => {
  pendingAceDraft.value = { ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz', loraRequiresReselection: true, loraName: 'Saved identity', loraScale: 0.4 }
  const container = await mount(); button(container, 'Capture current settings').click(); await settle()
  expect(capturedSettings[0]).toMatchObject({ loraRequiresReselection: true, loraName: 'Saved identity', loraScale: 0.4 })
  expect(submit).not.toHaveBeenCalled()
  expect(aceApi.loraLoad).not.toHaveBeenCalled()
})

it('preserves selected adapter identity in presets during loading, debounce and an in-flight strength change', async () => {
  localStorage.setItem('aicollector_ace_loras_v1', JSON.stringify([{ name: 'Fixture', path: '/fixture' }]))
  let finishLoad: (() => void) | undefined
  vi.mocked(aceApi.loraLoad).mockImplementationOnce(() => new Promise<void>(resolve => {
    finishLoad = () => { nativeLora = { lora_loaded: true, use_lora: true, lora_scale: 1, active_adapter: 'Fixture', adapters: ['Fixture'] }; resolve() }
  }))
  jazzDraft(); const container = await mount(); await choose(container, 'path:/fixture')
  button(container, 'Capture current settings').click(); await settle()
  expect(capturedSettings[0]).toMatchObject({ loraName: 'Fixture', loraRequiresReselection: true })
  finishLoad?.(); await settle()
  const strength = container.querySelector('#ace-lora-strength')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing strength')
  let finishScale: (() => void) | undefined
  vi.mocked(aceApi.loraScale).mockImplementationOnce(scale => new Promise<void>(resolve => { finishScale = () => { nativeLora = { ...nativeLora, lora_scale: scale }; resolve() } }))
  strength.value = '0.6'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  button(container, 'Capture current settings').click(); await settle()
  expect(capturedSettings[1]).toMatchObject({ loraName: 'Fixture', loraRequiresReselection: true, loraScale: 0.6 })
  await vi.advanceTimersByTimeAsync(300); await settle()
  button(container, 'Capture current settings').click(); await settle()
  expect(capturedSettings[2]).toMatchObject({ loraName: 'Fixture', loraRequiresReselection: true, loraScale: 0.6 })
  expect(button(container, 'Generate').disabled).toBe(true)
  finishScale?.(); await settle()
  expect(button(container, 'Generate').disabled).toBe(false)
})

it('refreshes a stale primary-model inventory after explicit LoRA selection before enabling generation', async () => {
  const cached: ModelInventory = { models: [{ name: 'model-A', is_default: true, is_loaded: true, supported_task_types: [] }, { name: 'model-B', is_default: false, is_loaded: false, supported_task_types: [] }], default_model: 'model-A', lm_models: [], loaded_lm_model: null, llm_initialized: false }
  state.inventory = cached
  nativeLora = { lora_loaded: true, use_lora: true, lora_scale: 0.75, active_adapter: 'Existing', adapters: ['Existing'] }
  pendingAceDraft.value = { ...emptyAceSettings(), mode: 'custom', customPrompt: 'Jazz', selectedModel: 'model-B', loraRequiresReselection: true, loraName: 'Existing', loraScale: 0.75 }
  let finish: (() => void) | undefined
  loadInventory.mockImplementationOnce(async () => {
    state.inventoryLoading = true
    await new Promise<void>(resolve => { finish = resolve })
    state.inventory = { ...cached, default_model: 'model-B' }; state.inventoryLoading = false
  })
  const container = await mount(); await choose(container, 'current')
  expect(loadInventory).toHaveBeenCalledTimes(1)
  expect(button(container, 'Generate').disabled).toBe(true)
  if (!finish) throw new Error('Inventory refresh did not start')
  finish(); await settle()
  expect(button(container, 'Generate').disabled).toBe(false)
  expect(container.querySelector('#ace-model')).toHaveProperty('value', 'model-B')
  expect(container.querySelector('#ace-model option[value="model-B"]')).toHaveProperty('disabled', false)
  state.inventoryError = 'inventory_unavailable'; await settle()
  expect(button(container, 'Generate').disabled).toBe(true)
  loadInventory.mockImplementationOnce(async () => { state.inventoryError = '' })
  button(container, 'Recheck LoRA status').click(); await settle()
  expect(loadInventory).toHaveBeenCalledTimes(2)
  expect(button(container, 'Generate').disabled).toBe(false)
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
