// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, reactive, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import AceStepPage from './AceStepPage.vue'
import { i18n, setLocale } from '../../i18n'
import * as voices from '../../api/voices'
import type { ModelInventory } from '../../api/aceStep'
import { voiceProfile } from '../voice/voiceTestFixtures'

const actions = vi.hoisted(() => {
  const inventory: ModelInventory = { models: [{ name: 'turbo', is_default: true, is_loaded: true, supported_task_types: ['cover'] }], default_model: 'turbo', lm_models: [], loaded_lm_model: null, llm_initialized: false }
  return { submit: vi.fn(), submitVoiceReplacement: vi.fn(), inventory, inventoryLoading: false, inventoryError: '', loadInventory: vi.fn(), pendingParamsInsert: null, clearPendingParamsInsert: vi.fn(), loadHistory: vi.fn(), startBackgroundTasks: vi.fn(), stopBackgroundTasks: vi.fn() }
})
const engine = reactive<{ statuses: { ace_step?: { status: 'running' | 'stopped'; error: null } } }>({ statuses: {} })
vi.mock('../../api/trackActivity', () => ({ listActiveAudioTrackIds: vi.fn().mockResolvedValue([]) }))
vi.mock('../../stores/aceStep', () => ({ useAceStepStore: () => actions }))
vi.mock('../../stores/orchestrator', () => ({ useOrchestratorStore: () => engine }))
vi.mock('../../api/voices', async (original) => ({ ...await original<typeof import('../../api/voices')>(), listVoices: vi.fn() }))
vi.mock('../../api/aceStepTraining', () => ({ trainingStatus: vi.fn().mockResolvedValue({ is_training: false }) }))
vi.mock('./ResultsFeed.vue', async () => { const { h } = await import('vue'); return { default: { render: () => h('div', { 'data-results': true }, 'Track list') } } })
vi.mock('../../components/shared/ModelOfflineBanner.vue', async () => { const { h } = await import('vue'); return { default: { render: () => h('div', 'Offline') } } })

let app: App | undefined
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); localStorage.clear(); setLocale('en'); engine.statuses = {}
  vi.mocked(voices.listVoices).mockResolvedValue([{ ...voiceProfile(), status: 'ready', usable: true }, { ...voiceProfile(), id: 'b'.repeat(32), name: 'Second voice', status: 'ready', usable: true }])
  voices.setActiveVoiceId(voiceProfile().id)
  actions.submit.mockResolvedValue(undefined); actions.submitVoiceReplacement.mockResolvedValue(undefined)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 6; index++) await nextTick() }
async function mount(running = true) {
  if (running) engine.statuses.ace_step = { status: 'running', error: null }
  app = createApp(AceStepPage).use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }, { path: '/voice-clone', component: { render: () => null } }] }))
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle(); return container
}
function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll('button')].find((node) => node.textContent?.trim() === label && node.style.display !== 'none')
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
function workspace(container: HTMLElement) {
  const found = container.querySelector('[data-generator-workspace]')
  if (!(found instanceof HTMLElement)) throw new Error('Missing workspace')
  return found
}
async function upload(container: HTMLElement) {
  const label = [...container.querySelectorAll('label')].find((node) => node.textContent?.includes('Reference track / remix'))
  const toggle = label?.querySelector('input')
  if (!(toggle instanceof HTMLInputElement)) throw new Error('Missing reference toggle')
  toggle.click(); await settle()
  const input = container.querySelector('input[aria-label="Reference track file"]')
  if (!(input instanceof HTMLInputElement)) throw new Error('Missing file input')
  const file = new File(['fixture'], 'client-draft.wav', { type: 'audio/wav' })
  Object.defineProperty(input, 'files', { configurable: true, value: [file] }); input.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  return { input, file }
}

it('uses full-width results when hidden or floating and retains the actual prompt and native file input', async () => {
  const container = await mount()
  const prompt = container.querySelector('textarea')
  if (!(prompt instanceof HTMLTextAreaElement)) throw new Error('Missing prompt')
  prompt.value = 'Quiet piano'; prompt.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  const { input, file } = await upload(container)
  expect(workspace(container).classList.contains('lg:grid-cols-[480px_minmax(0,1fr)]')).toBe(true)
  button(container, 'Hide generator').click(); await settle()
  expect(workspace(container).classList.contains('lg:grid-cols-[480px_minmax(0,1fr)]')).toBe(false)
  expect(container.querySelector('[role=region]')?.getAttribute('style')).toContain('display: none')
  button(container, 'Float generator').click(); await settle()
  expect(workspace(container).classList.contains('lg:grid-cols-[480px_minmax(0,1fr)]')).toBe(false)
  expect(container.querySelector('[data-results]')?.textContent).toBe('Track list')
  button(container, 'Dock generator').click(); await settle()
  expect(workspace(container).classList.contains('lg:grid-cols-[480px_minmax(0,1fr)]')).toBe(true)
  expect(container.querySelector('textarea')).toBe(prompt); expect(prompt.value).toBe('Quiet piano')
  expect(container.querySelector('input[aria-label="Reference track file"]')).toBe(input); expect(input.files?.[0]).toBe(file)
  expect(actions.loadHistory).toHaveBeenCalledTimes(1); expect(actions.startBackgroundTasks).toHaveBeenCalledTimes(1); expect(actions.stopBackgroundTasks).not.toHaveBeenCalled()
})

it('preserves real custom lyrics, voice choice and a pending ACE submission across all panel modes', async () => {
  let finish: (() => void) | undefined
  actions.submit.mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
  const container = await mount()
  button(container, i18n.global.t('aceGen.modeCustom')).click(); await settle()
  const prompt = container.querySelector(`input[placeholder="${i18n.global.t('aceGen.stylePlaceholder')}"]`)
  const lyrics = container.querySelector('textarea')
  if (!(prompt instanceof HTMLInputElement) || !(lyrics instanceof HTMLTextAreaElement)) throw new Error('Missing custom inputs')
  prompt.value = 'Piano jazz'; prompt.dispatchEvent(new Event('input', { bubbles: true })); prompt.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  lyrics.value = '[Verse]\nA retained draft'; lyrics.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  const voiceLabel = [...container.querySelectorAll('label')].find((node) => node.textContent?.includes('Voice for new songs'))
  const voice = voiceLabel?.querySelector('select')
  if (!(voice instanceof HTMLSelectElement)) throw new Error('Missing voice selection')
  voice.value = 'b'.repeat(32); voice.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  const { input, file } = await upload(container)
  const submit = button(container, 'Generate'); submit.click(); await settle()
  expect(actions.submit).toHaveBeenCalledOnce()
  expect(actions.submit).toHaveBeenCalledWith(expect.objectContaining({ prompt: 'Piano jazz', lyrics: '[Verse]\nA retained draft', task_type: 'cover' }), file, 'Piano jazz', null, expect.objectContaining({ customLyrics: '[Verse]\nA retained draft' }))
  button(container, 'Hide generator').click(); await settle(); button(container, 'Float generator').click(); await settle(); button(container, 'Dock generator').click(); await settle()
  expect(submit.disabled).toBe(true); expect(input.disabled).toBe(true); expect(container.querySelector('textarea')).toBe(lyrics); expect(lyrics.value).toBe('[Verse]\nA retained draft')
  expect(container.querySelector('input[aria-label="Reference track file"]')).toBe(input); expect(input.files?.[0]).toBe(file); expect(voice.value).toBe('b'.repeat(32)); expect(voices.getActiveVoiceId()).toBe('b'.repeat(32))
  if (!finish) throw new Error('Submission did not start')
  finish(); await settle()
  expect(submit.disabled).toBe(false); expect(input.disabled).toBe(false); expect(actions.submit).toHaveBeenCalledOnce()
})

it('keeps ordinary generation at unknown startup and enables it after actual running status arrives', async () => {
  const container = await mount(false)
  const prompt = container.querySelector('textarea')
  if (!(prompt instanceof HTMLTextAreaElement)) throw new Error('Unknown startup must retain ordinary controls')
  expect(button(container, 'Generate').disabled).toBe(true)
  prompt.value = 'A startup draft'; prompt.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  button(container, 'Float generator').click(); await settle()
  engine.statuses.ace_step = { status: 'running', error: null }; await settle()
  expect(container.querySelector('textarea')).toBe(prompt); expect(prompt.value).toBe('A startup draft'); expect(button(container, 'Generate').disabled).toBe(false)
  expect(container.textContent).not.toContain('Replace voice')
})

it('closes child help on Escape without hiding the floating generator', async () => {
  const container = await mount(); button(container, 'Float generator').click(); await settle()
  const label = [...container.querySelectorAll('label')].find((node) => node.textContent?.includes('Reference track / remix'))
  const opener = label?.querySelector('button')
  if (!(opener instanceof HTMLButtonElement)) throw new Error('Missing help opener')
  opener.focus(); opener.click(); await settle()
  const dialog = container.querySelector('[role=dialog][aria-modal=true]')
  if (!(dialog instanceof HTMLElement)) throw new Error('Missing real help dialog')
  expect(document.activeElement).toBe(dialog.querySelector('button'))
  const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }); document.activeElement?.dispatchEvent(escape); await settle()
  expect(escape.defaultPrevented).toBe(true); expect(container.querySelector('[role=dialog]')).toBeNull()
  expect(document.activeElement).toBe(opener)
  expect(container.querySelector('[role=region]')?.classList.contains('generator-panel-floating')).toBe(true)
  expect(container.querySelector('[role=region]')?.getAttribute('style') ?? '').not.toContain('display: none')
})
