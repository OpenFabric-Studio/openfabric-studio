// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import * as api from '../../api/musicLora'
import type { MusicAdapter, MusicAdapterSelection } from '../../api/generated'
import MusicAdapterSelect from './MusicAdapterSelect.vue'
vi.mock('../../api/musicLora', () => ({ listMusicAdapters: vi.fn() }))
let app: App | undefined
const adapter: MusicAdapter = { id: 'a'.repeat(32), engine: 'stable_audio3', target: 'small-music', name: 'Piano', sha256: 'b'.repeat(64), created_at: 'now', source_job_id: null }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.listMusicAdapters).mockResolvedValue({ adapters: [adapter, { ...adapter, id: 'c'.repeat(32), target: 'medium', name: 'Medium only' }, { ...adapter, id: 'd'.repeat(32), engine: 'yue2', target: 'ar', name: 'YuE AR' }], history_incomplete: false }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let index = 0; index < 12; index++) await nextTick() }
async function mount(initial: MusicAdapterSelection | null = null) {
  const model = ref<MusicAdapterSelection | null>(initial), target = ref<MusicAdapter['target']>('small-music'), disabled = ref(false)
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(MusicAdapterSelect, { engine: 'stable_audio3', target: target.value, disabled: disabled.value, modelValue: model.value, 'onUpdate:modelValue': (value: MusicAdapterSelection | null) => { model.value = value } }) }).use(i18n)
  app.mount(node); await settle(); return { node, model, target, disabled }
}
async function choose(node: HTMLElement, id: string) { const select = node.querySelector('select'); if (!select) throw new Error('Missing selector'); select.value = id; select.dispatchEvent(new Event('change', { bubbles: true })); await settle() }
it('lists only compatible managed adapters and emits an ID and bounded strength', async () => {
  const { node, model } = await mount()
  expect(node.textContent).toContain('Piano'); expect(node.textContent).not.toContain('Medium only'); expect(node.textContent).not.toContain('YuE AR')
  await choose(node, adapter.id); expect(model.value).toEqual({ id: adapter.id, strength: 1 })
  const input = node.querySelector('input'); if (!input) throw new Error('Missing strength')
  input.value = '0.5'; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(model.value).toEqual({ id: adapter.id, strength: 0.5 })
  input.value = '3'; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(model.value).toEqual({ id: adapter.id, strength: 0.5 })
  expect(input.validity.rangeOverflow).toBe(true)
  expect(node.textContent).toContain('Enter a strength between 0 and 2')
  input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(model.value).toEqual({ id: adapter.id, strength: 0.5 })
  expect(input.validity.valueMissing).toBe(true)
  input.value = '-1'; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(model.value).toEqual({ id: adapter.id, strength: 0.5 })
  expect(input.validity.rangeUnderflow).toBe(true)
})
it('clears a stale selection when the base model changes and respects disabled controls', async () => {
  const { node, model, target, disabled } = await mount({ id: adapter.id, strength: 1.2 })
  target.value = 'medium'; await settle(); expect(model.value).toBeNull(); expect(node.textContent).toContain('Medium only')
  disabled.value = true; await settle(); expect(node.querySelector('select')?.disabled).toBe(true)
})
it('clears selections missing from the restored registry and reports safe load errors', async () => {
  const { model, node: restored } = await mount({ id: 'e'.repeat(32), strength: 1 })
  expect(model.value).toBeNull()
  expect(restored.textContent).toContain('The previous adapter is unavailable or incompatible and was turned off')
  app?.unmount(); app = undefined; document.body.replaceChildren()
  vi.mocked(api.listMusicAdapters).mockRejectedValue(new Error('/private/token'))
  const { node } = await mount()
  expect(node.textContent).toContain('Could not load music adapters'); expect(node.textContent).not.toContain('/private/token')
})
it('aborts registry reads and ignores a late response after teardown', async () => {
  let finish: ((value: { adapters: MusicAdapter[]; history_incomplete: boolean }) => void) | undefined
  vi.mocked(api.listMusicAdapters).mockReturnValue(new Promise(resolve => { finish = resolve }))
  await mount()
  const signal = vi.mocked(api.listMusicAdapters).mock.calls[0]?.[0]
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  finish?.({ adapters: [adapter], history_incomplete: false }); await settle()
  expect(api.listMusicAdapters).toHaveBeenCalledTimes(1)
})
