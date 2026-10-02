// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, onMounted, onUnmounted, ref, type App } from 'vue'
import GeneratorPanel, { type GeneratorPanelMode } from './GeneratorPanel.vue'
import { i18n, setLocale } from '../../i18n'

let app: App | undefined
let mounts = 0
let unmounts = 0
const storageKey = 'remiqora:ace-generator-panel'

beforeEach(() => { localStorage.clear(); setLocale('en'); mounts = 0; unmounts = 0 })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); document.documentElement.style.removeProperty('--app-header-height'); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function settle() { for (let index = 0; index < 4; index++) await nextTick() }
function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll('button')].find((node) => node.textContent?.trim() === label && node.style.display !== 'none')
  if (!found) throw new Error(`Missing visible button: ${label}`)
  return found
}
function region(container: HTMLElement) {
  const found = container.querySelector('[role=region]')
  if (!(found instanceof HTMLElement)) throw new Error('Missing generator region')
  return found
}
async function mount() {
  const mode = ref<GeneratorPanelMode>('docked')
  const modalOpen = ref(false)
  const draft = defineComponent({
    setup() {
      const prompt = ref('')
      const lyrics = ref('')
      const voice = ref('voice-one')
      const file = ref<File | null>(null)
      onMounted(() => { mounts++ }); onUnmounted(() => { unmounts++ })
      return () => h('div', [
        h('textarea', { 'aria-label': 'Prompt', value: prompt.value, onInput: (event: Event) => { if (event.target instanceof HTMLTextAreaElement) prompt.value = event.target.value } }),
        h('textarea', { 'aria-label': 'Lyrics', value: lyrics.value, onInput: (event: Event) => { if (event.target instanceof HTMLTextAreaElement) lyrics.value = event.target.value } }),
        h('select', { 'aria-label': 'Voice', value: voice.value, onChange: (event: Event) => { if (event.target instanceof HTMLSelectElement) voice.value = event.target.value } }, [h('option', { value: 'voice-one' }, 'One'), h('option', { value: 'voice-two' }, 'Two')]),
        h('input', { type: 'file', onChange: (event: Event) => { if (event.target instanceof HTMLInputElement) file.value = event.target.files?.[0] ?? null } }),
        h('p', { 'data-file': true }, file.value?.name),
        modalOpen.value ? h('div', { role: 'dialog', 'aria-modal': 'true' }, 'Help') : null,
      ])
    },
  })
  app = createApp({ setup: () => () => h('div', [
    h(GeneratorPanel, { modelValue: mode.value, 'onUpdate:modelValue': (value: GeneratorPanelMode) => { mode.value = value } }, { default: () => h(draft) }),
    h('button', { 'data-result': true }, 'Result action'),
  ]) }).use(i18n)
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  return { container, mode, modalOpen }
}

it('starts docked, hides the whole region, and reopens docked or floating', async () => {
  const { container, mode } = await mount()
  expect(region(container).getAttribute('aria-label')).toBe('Generator')
  expect(mode.value).toBe('docked')
  button(container, 'Hide generator').click(); await settle()
  expect(mode.value).toBe('hidden'); expect(region(container).style.display).toBe('none')
  button(container, 'Show generator').click(); await settle()
  expect(mode.value).toBe('docked'); expect(region(container).style.display).not.toBe('none')
  button(container, 'Float generator').click(); await settle()
  expect(mode.value).toBe('floating'); expect(region(container).classList.contains('generator-panel-floating')).toBe(true)
  expect(region(container).getAttribute('aria-modal')).toBeNull()
  button(region(container), 'Dock generator').click(); await settle()
  expect(mode.value).toBe('docked')
})

it('keeps one mounted draft, including native upload and voice selection, through every mode', async () => {
  const { container } = await mount()
  const prompt = container.querySelector('[aria-label=Prompt]')
  const lyrics = container.querySelector('[aria-label=Lyrics]')
  const voice = container.querySelector('[aria-label=Voice]')
  const upload = container.querySelector('input[type=file]')
  if (!(prompt instanceof HTMLTextAreaElement) || !(lyrics instanceof HTMLTextAreaElement) || !(voice instanceof HTMLSelectElement) || !(upload instanceof HTMLInputElement)) throw new Error('Missing draft controls')
  prompt.value = 'Jazz'; prompt.dispatchEvent(new Event('input')); lyrics.value = 'One verse'; lyrics.dispatchEvent(new Event('input'))
  voice.value = 'voice-two'; voice.dispatchEvent(new Event('change'))
  const file = new File(['audio'], 'draft.wav', { type: 'audio/wav' })
  Object.defineProperty(upload, 'files', { configurable: true, value: [file] }); upload.dispatchEvent(new Event('change')); await settle()
  button(container, 'Hide generator').click(); await settle(); button(container, 'Float generator').click(); await settle()
  button(region(container), 'Dock generator').click(); await settle()
  expect(container.querySelector('[aria-label=Prompt]')).toBe(prompt); expect(prompt.value).toBe('Jazz'); expect(lyrics.value).toBe('One verse')
  expect(voice.value).toBe('voice-two'); expect(container.querySelector('input[type=file]')).toBe(upload); expect(upload.files?.[0]).toBe(file)
  expect(container.querySelector('[data-file]')?.textContent).toBe('draft.wav'); expect(mounts).toBe(1); expect(unmounts).toBe(0)
})

it('opens with a focus target, consumes Escape from the floating form, and returns focus to Show', async () => {
  const { container, mode } = await mount()
  const trigger = button(container, 'Float generator'); trigger.focus(); trigger.click(); await settle()
  expect(region(container).contains(document.activeElement)).toBe(true)
  const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  document.activeElement?.dispatchEvent(escape); await settle()
  expect(escape.defaultPrevented).toBe(true); expect(mode.value).toBe('hidden'); expect(document.activeElement).toBe(button(container, 'Show generator'))
  button(container, 'Show generator').click(); await settle()
  expect(region(container).contains(document.activeElement)).toBe(true)
})

it('leaves the results keyboard accessible and ignores their Escape and an open child modal', async () => {
  const { container, mode, modalOpen } = await mount()
  button(container, 'Float generator').click(); await settle()
  const result = container.querySelector('[data-result]')
  if (!(result instanceof HTMLButtonElement)) throw new Error('Missing results action')
  result.focus(); result.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await settle()
  expect(mode.value).toBe('floating'); expect(document.activeElement).toBe(result)
  modalOpen.value = true; await settle()
  region(container).querySelector('textarea')?.focus()
  const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }); document.activeElement?.dispatchEvent(escape); await settle()
  expect(mode.value).toBe('floating'); expect(escape.defaultPrevented).toBe(false)
  modalOpen.value = false; await settle()
  const consumed = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }); consumed.preventDefault(); document.activeElement?.dispatchEvent(consumed); await settle()
  expect(mode.value).toBe('floating')
})

it.each(['hidden', 'floating'] as const)('restores and persists the validated %s mode', async (saved) => {
  localStorage.setItem(storageKey, saved)
  const { container, mode } = await mount()
  expect(mode.value).toBe(saved)
  if (saved === 'hidden') button(container, 'Show generator').click()
  else button(region(container), 'Dock generator').click()
  await settle(); expect(localStorage.getItem(storageKey)).toBe('docked')
})

it.each(['bogus', '{"mode":"floating"}'])('rejects an invalid stored mode %s', async (saved) => {
  localStorage.setItem(storageKey, saved)
  const { mode } = await mount(); expect(mode.value).toBe('docked')
})

it('still operates when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Blocked') })
  const { container, mode } = await mount(); button(container, 'Float generator').click(); await settle()
  expect(mode.value).toBe('floating')
})

it('removes its Escape listener at teardown', async () => {
  const added = vi.spyOn(window, 'addEventListener')
  const removed = vi.spyOn(window, 'removeEventListener')
  const { container, mode } = await mount(); button(container, 'Float generator').click(); await settle()
  const listener = added.mock.calls.find(([name]) => name === 'keydown')?.[1]
  expect(listener).toBeDefined()
  app?.unmount(); app = undefined
  expect(removed).toHaveBeenCalledWith('keydown', listener)
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })); await settle()
  expect(mode.value).toBe('floating'); expect(unmounts).toBe(1)
})

async function floatingWithBounds(appHeader?: HTMLElement) {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1200)
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(900)
  document.documentElement.style.setProperty('--app-header-height', '80px')
  let resize: (() => void) | undefined
  const disconnect = vi.fn()
  const observed = vi.fn<(element: Element) => void>()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resize = () => callback([], this) }
    observe(element: Element) { observed(element) }
    unobserve() {}
    disconnect() { disconnect() }
  })
  if (appHeader) document.body.prepend(appHeader)
  const { container, mode } = await mount()
  const panel = region(container)
  const size = { width: 480, height: 600 }
  vi.spyOn(panel, 'getBoundingClientRect').mockImplementation(() => new DOMRect(
    Number.parseFloat(panel.style.left) || 704,
    Number.parseFloat(panel.style.top) || 92,
    size.width, size.height,
  ))
  const header = panel.firstElementChild
  if (!(header instanceof HTMLElement)) throw new Error('Missing floating header')
  const capture = vi.fn()
  const release = vi.fn()
  Object.defineProperty(header, 'setPointerCapture', { configurable: true, value: capture })
  Object.defineProperty(header, 'hasPointerCapture', { configurable: true, value: () => true })
  Object.defineProperty(header, 'releasePointerCapture', { configurable: true, value: release })
  button(container, 'Float generator').click(); await settle()
  return { container, panel, header, mode, size, capture, release, disconnect, observed, resize: () => { if (!resize) throw new Error('Missing ResizeObserver'); resize() } }
}
function pointer(target: HTMLElement, type: string, x: number, y: number, pointerId = 7, pointerType = 'mouse', buttonIndex = 0) {
  const event = new PointerEvent(type, { clientX: x, clientY: y, pointerId, pointerType, button: buttonIndex, isPrimary: true, bubbles: true, cancelable: true })
  target.dispatchEvent(event)
  return event
}
function position(panel: HTMLElement) { return { x: Number.parseFloat(panel.style.left), y: Number.parseFloat(panel.style.top) } }

it.each(['mouse', 'touch', 'pen'])('moves the floating header with %s pointer capture and releases on pointerup', async (kind) => {
  const { panel, header, capture, release } = await floatingWithBounds()
  expect(position(panel)).toEqual({ x: 704, y: 92 })
  const start = pointer(header, 'pointerdown', 750, 110, 7, kind)
  expect(start.defaultPrevented).toBe(true); expect(capture).toHaveBeenCalledWith(7)
  pointer(header, 'pointermove', 450, 200, 8, kind); await settle()
  expect(position(panel)).toEqual({ x: 704, y: 92 })
  pointer(header, 'pointermove', 450, 210, 7, kind); await settle()
  expect(position(panel)).toEqual({ x: 404, y: 192 })
  pointer(header, 'pointerup', 450, 210, 7, kind)
  expect(release).toHaveBeenCalledWith(7)
  pointer(header, 'pointermove', 700, 300, 7, kind); await settle()
  expect(position(panel)).toEqual({ x: 404, y: 192 })
})

it('clamps pointer movement to screen edges and below the fixed application header', async () => {
  const { panel, header } = await floatingWithBounds()
  pointer(header, 'pointerdown', 750, 110)
  pointer(header, 'pointermove', -10000, -10000); await settle()
  expect(position(panel)).toEqual({ x: 12, y: 92 })
  pointer(header, 'pointermove', 10000, 10000); await settle()
  expect(position(panel)).toEqual({ x: 708, y: 288 })
})

it.each(['pointercancel', 'lostpointercapture'])('stops movement on %s without discarding the last position', async (type) => {
  const { panel, header } = await floatingWithBounds()
  pointer(header, 'pointerdown', 750, 110); pointer(header, 'pointermove', 450, 210); await settle()
  pointer(header, type, 450, 210); pointer(header, 'pointermove', 900, 400); await settle()
  expect(position(panel)).toEqual({ x: 404, y: 192 })
})

it('ignores interactive header controls, secondary mouse buttons and secondary pointers', async () => {
  const { panel, header, capture } = await floatingWithBounds()
  pointer(button(panel, 'Dock generator'), 'pointerdown', 900, 110)
  pointer(header, 'pointerdown', 750, 110, 7, 'mouse', 2)
  header.dispatchEvent(new PointerEvent('pointerdown', { pointerId: 8, isPrimary: false, bubbles: true, cancelable: true }))
  pointer(header, 'pointermove', 450, 210); await settle()
  expect(capture).not.toHaveBeenCalled(); expect(position(panel)).toEqual({ x: 704, y: 92 })
})

it('offers named keyboard movement with small and larger steps and the same bounds', async () => {
  const { panel } = await floatingWithBounds()
  const handle = panel.querySelector('[aria-label="Move generator"]')
  if (!(handle instanceof HTMLButtonElement)) throw new Error('Missing accessible movement handle')
  expect(handle.getAttribute('aria-describedby')).toBe(panel.getAttribute('aria-describedby'))
  handle.focus()
  const left = new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }); handle.dispatchEvent(left); await settle()
  expect(left.defaultPrevented).toBe(true); expect(position(panel)).toEqual({ x: 694, y: 92 })
  handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', shiftKey: true, bubbles: true, cancelable: true })); await settle()
  expect(position(panel)).toEqual({ x: 694, y: 142 })
  for (let index = 0; index < 30; index++) handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', shiftKey: true, bubbles: true, cancelable: true }))
  await settle(); expect(position(panel).y).toBe(92); expect(document.activeElement).toBe(handle)
})

it('reclamps after viewport resize and panel content size changes', async () => {
  const { panel, header, size, resize } = await floatingWithBounds()
  pointer(header, 'pointerdown', 750, 110); pointer(header, 'pointermove', 10000, 10000); pointer(header, 'pointerup', 10000, 10000); await settle()
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(700); vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(800)
  window.dispatchEvent(new Event('resize')); await settle()
  expect(position(panel)).toEqual({ x: 208, y: 188 })
  size.height = 680; resize(); await settle()
  expect(position(panel)).toEqual({ x: 208, y: 108 })
  document.documentElement.style.setProperty('--app-header-height', '120px'); size.height = 640; resize(); await settle()
  expect(position(panel)).toEqual({ x: 208, y: 132 })
})

it('observes growing application chrome even when the capped panel size stays unchanged', async () => {
  const appHeader = document.createElement('header')
  const { panel, observed, resize } = await floatingWithBounds(appHeader)
  expect(observed).toHaveBeenCalledWith(appHeader)
  document.documentElement.style.setProperty('--app-header-height', '140px')
  resize(); await settle()
  expect(position(panel)).toEqual({ x: 704, y: 152 })
})

it.each(['hidden', 'docked'] as const)('ends capture on %s and retains the session position when floating again', async (next) => {
  const stored = vi.spyOn(Storage.prototype, 'setItem')
  const { container, panel, header, release } = await floatingWithBounds()
  const prompt = container.querySelector('textarea')
  pointer(header, 'pointerdown', 750, 110); pointer(header, 'pointermove', 450, 210); await settle()
  button(panel, next === 'hidden' ? 'Hide generator' : 'Dock generator').click(); await settle()
  expect(release).toHaveBeenCalledWith(7)
  pointer(header, 'pointermove', 900, 400); await settle()
  button(container, 'Float generator').click(); await settle()
  expect(position(panel)).toEqual({ x: 404, y: 192 }); expect(container.querySelector('textarea')).toBe(prompt); expect(mounts).toBe(1)
  expect(localStorage.getItem(storageKey)).toBe('floating')
  expect(stored.mock.calls.every(([key]) => key === storageKey)).toBe(true)
})

it('releases active capture and removes resize observation and listeners at unmount', async () => {
  const removed = vi.spyOn(window, 'removeEventListener')
  const { header, release, disconnect } = await floatingWithBounds()
  pointer(header, 'pointerdown', 750, 110)
  app?.unmount(); app = undefined
  expect(release).toHaveBeenCalledWith(7); expect(disconnect).toHaveBeenCalledOnce()
  expect(removed.mock.calls.some(([name]) => name === 'resize')).toBe(true)
})
