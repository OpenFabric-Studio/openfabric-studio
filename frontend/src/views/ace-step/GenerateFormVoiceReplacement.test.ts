// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import GenerateForm from './GenerateForm.vue'
import { i18n, setLocale } from '../../i18n'
import * as voices from '../../api/voices'
import type { ModelInventory } from '../../api/aceStep'
import { voiceProfile } from '../voice/voiceTestFixtures'

const actions = vi.hoisted(() => {
  const inventory: ModelInventory | null = { models: [{ name: 'turbo', is_default: true, is_loaded: true, supported_task_types: ['cover'] }], default_model: 'turbo', lm_models: [], loaded_lm_model: null, llm_initialized: false }
  return {
  submit: vi.fn(), submitVoiceReplacement: vi.fn(),
  inventory: (() : ModelInventory | null => inventory)(), inventoryLoading: false, inventoryError: '', loadInventory: vi.fn(),
  pendingParamsInsert: null, clearPendingParamsInsert: vi.fn(),
}})
const initialInventory = actions.inventory
vi.mock('../../stores/aceStep', () => ({ useAceStepStore: () => actions }))
vi.mock('../../api/voices', async (original) => ({ ...await original<typeof import('../../api/voices')>(), listVoices: vi.fn(), getActiveVoiceId: vi.fn() }))

let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); localStorage.clear(); setLocale('en')
  vi.mocked(voices.listVoices).mockResolvedValue([{ ...voiceProfile(), status: 'ready', usable: true }])
  vi.mocked(voices.getActiveVoiceId).mockReturnValue(voiceProfile().id)
  actions.submit.mockResolvedValue(undefined); actions.submitVoiceReplacement.mockResolvedValue(undefined)
  actions.inventory = initialInventory; actions.inventoryLoading = false; actions.inventoryError = ''
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 6; index++) await nextTick() }
async function mount(generationAvailable = true) {
  app = createApp(GenerateForm, { generationAvailable }).use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }, { path: '/voice-clone', component: { render: () => null } }] }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle(); return container
}
function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll('button')].find((node) => node.textContent?.trim() === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
async function reference(container: HTMLElement) {
  const label = [...container.querySelectorAll('label')].find((node) => node.textContent?.includes('Reference track / remix'))
  const toggle = label?.querySelector('input')
  if (!(toggle instanceof HTMLInputElement)) throw new Error('Missing reference toggle')
  toggle.click(); await settle()
}
async function upload(container: HTMLElement) {
  const input = container.querySelector('input[aria-label="Reference track file"]')
  if (!(input instanceof HTMLInputElement)) throw new Error('Missing upload')
  const file = new File(['fixture'], 'original-song.wav', { type: 'audio/wav' })
  Object.defineProperty(input, 'files', { configurable: true, value: [file] })
  input.dispatchEvent(new Event('change', { bubbles: true })); await settle(); return file
}
async function replacement(container: HTMLElement) { await reference(container); button(container, 'Voice Replacement').click(); await settle() }

it('offers replacement independently of ACE task support and hides irrelevant generation controls', async () => {
  const container = await mount(); await replacement(container)
  expect(button(container, 'Voice Replacement').disabled).toBe(false)
  expect(button(container, 'Replace voice').disabled).toBe(true)
  expect(container.textContent).toContain('original accompaniment')
  expect(container.querySelector('textarea')).toBeNull()
  expect(container.textContent).not.toContain('Number of variants')
  expect(container.textContent).not.toContain('Advanced settings')
  expect(container.querySelector('input[aria-label="Reference track file"]')?.getAttribute('aria-label')).toBe('Reference track file')
})

it('replaces the uploaded song with the selected ready voice without requiring a style or generating music', async () => {
  const container = await mount(); await replacement(container); const file = await upload(container)
  expect(button(container, 'Replace voice').disabled).toBe(false)
  button(container, 'Replace voice').click(); await settle()
  expect(actions.submitVoiceReplacement).toHaveBeenCalledWith(file, voiceProfile().id)
  expect(actions.submit).not.toHaveBeenCalled()
})

it('requires a usable selected voice even when an old voice ID is stored', async () => {
  vi.mocked(voices.listVoices).mockResolvedValue([{ ...voiceProfile(), usable: false }])
  const container = await mount(); await replacement(container); await upload(container)
  expect(button(container, 'Replace voice').disabled).toBe(true)
  expect(container.textContent).toContain('Select a ready cloned voice')
  expect(actions.submitVoiceReplacement).not.toHaveBeenCalled()
})

it('reserves a replacement submission before another click and shows a structured failure', async () => {
  let reject: (cause: Error) => void = () => { throw new Error('Not initialized') }
  actions.submitVoiceReplacement.mockReturnValue(new Promise<void>((_, fail) => { reject = fail }))
  const container = await mount(); await replacement(container); await upload(container)
  const action = button(container, 'Replace voice'); action.click(); action.click(); await settle()
  expect(actions.submitVoiceReplacement).toHaveBeenCalledTimes(1)
  reject(new Error('The voice is not ready.')); await settle()
  expect(container.querySelector('[role=alert]')?.textContent).toContain('The voice is not ready.')
  expect(button(container, 'Replace voice').disabled).toBe(false)
})

it('continues submitting ordinary covers through ACE with their style and reference file', async () => {
  const container = await mount(); await reference(container); const file = await upload(container)
  const text = container.querySelector('textarea')
  if (!(text instanceof HTMLTextAreaElement)) throw new Error('Missing style prompt')
  text.value = 'Piano jazz'; text.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  button(container, 'Generate').click(); await settle()
  expect(actions.submit).toHaveBeenCalledWith(expect.objectContaining({ task_type: 'cover', prompt: 'Piano jazz' }), file, 'Piano jazz', null, expect.objectContaining({ useRefAudio: true }))
  expect(actions.submitVoiceReplacement).not.toHaveBeenCalled()
})

it('documents voice replacement in reference track help', async () => {
  const container = await mount()
  const referenceLabel = [...container.querySelectorAll('label')].find((node) => node.textContent?.includes('Reference track / remix'))
  referenceLabel?.querySelector('button')?.click(); await settle()
  const heading = [...container.querySelectorAll('h3')].find((node) => node.textContent === 'Reference track / remix')
  expect(heading?.parentElement?.parentElement?.textContent).toContain('Voice Replacement')
  expect(heading?.parentElement?.parentElement?.textContent).toContain('cloned voice')
})

it('names reference help as a dialog and consumes Escape before outer panel controls', async () => {
  const container = await mount()
  const referenceLabel = [...container.querySelectorAll('label')].find((node) => node.textContent?.includes('Reference track / remix'))
  referenceLabel?.querySelector('button')?.click(); await settle()
  const dialog = container.querySelector('[role=dialog][aria-modal=true]')
  expect(dialog?.getAttribute('aria-label')).toBe('Reference track / remix')
  const escape = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
  window.dispatchEvent(escape); await settle()
  expect(escape.defaultPrevented).toBe(true)
  expect(container.querySelector('[role=dialog]')).toBeNull()
})

it('shows inventory failure and retry instead of a blank actionable model selector', async () => {
  actions.inventory = null; actions.inventoryError = 'inventory_unavailable'
  const container = await mount()
  expect(container.textContent).toContain('Could not load the available models')
  const select = container.querySelector('[aria-label="Model"]')
  expect(select).toBeInstanceOf(HTMLSelectElement)
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing model selection')
  expect(select.disabled).toBe(true)
  button(container, 'Retry loading models').click()
  expect(actions.loadInventory).toHaveBeenCalledTimes(1)
})

it('selects a real installed model when the native inventory has no default', async () => {
  actions.inventory = { models: [{ name: 'xl-base', is_default: false, is_loaded: false, supported_task_types: ['cover'] }, { name: 'xl-sft', is_default: false, is_loaded: false, supported_task_types: ['cover'] }], default_model: '', lm_models: [], loaded_lm_model: null, llm_initialized: false }
  const container = await mount()
  const select = container.querySelector('[aria-label="Model"]')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing model selection')
  expect(select.value).toBe('xl-base')
  select.value = 'xl-sft'; select.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  const text = container.querySelector('textarea')
  if (!(text instanceof HTMLTextAreaElement)) throw new Error('Missing prompt')
  text.value = 'Jazz'; text.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  button(container, 'Generate').click(); await settle()
  expect(actions.submit).toHaveBeenCalledWith(expect.objectContaining({ model: 'xl-sft' }), null, 'Jazz', null, expect.objectContaining({ selectedModel: 'xl-sft' }))
})

it('allows uploaded voice replacement while ACE generation is unavailable', async () => {
  const container = await mount(false)
  expect(button(container, 'Replace voice').disabled).toBe(true)
  await upload(container)
  expect(button(container, 'Replace voice').disabled).toBe(false)
  expect(button(container, 'Cover').disabled).toBe(true)
  button(container, 'Replace voice').click(); await settle()
  expect(actions.submitVoiceReplacement).toHaveBeenCalledTimes(1)
  expect(actions.submit).not.toHaveBeenCalled()
})

it('keeps ordinary generation selected while the initial engine status is still unknown', async () => {
  const availability = ref<boolean | null>(null)
  app = createApp({ render: () => h(GenerateForm, { generationAvailable: availability.value }) })
    .use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }, { path: '/voice-clone', component: { render: () => null } }] }))
  const container = document.body.appendChild(document.createElement('div'))
  app.mount(container); await settle()
  expect(container.querySelector('textarea')).toBeInstanceOf(HTMLTextAreaElement)
  expect(button(container, 'Generate').disabled).toBe(true)
  availability.value = true; await settle()
  expect(container.querySelector('textarea')).toBeInstanceOf(HTMLTextAreaElement)
  expect(container.textContent).not.toContain('Replace voice')
})
