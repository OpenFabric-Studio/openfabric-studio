// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import type { VoiceMixSettings } from '../../api/generated'
import VoiceMixControls from './VoiceMixControls.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 6; i++) await nextTick() }
async function mount(value: VoiceMixSettings | null = null) {
  const model = ref<VoiceMixSettings | null>(value), valid = ref(true)
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(VoiceMixControls, { modelValue: model.value, 'onUpdate:modelValue': next => { model.value = next }, onValid: next => { valid.value = next } }) }).use(i18n)
  app.mount(node); await settle(); return { node, model, valid }
}
function input(node: HTMLElement, label: string) { const field = [...node.querySelectorAll<HTMLInputElement>('input')].find(item => item.parentElement?.textContent?.trim() === label); if (!field) throw new Error(`Missing ${label}`); return field }
it('keeps legacy mixing until the explicit checkbox is selected', async () => {
  const { node, model } = await mount(); expect(model.value).toBeNull()
  input(node, 'Use measured vocal mixing').click(); await settle()
  expect(model.value).toEqual({ vocal_level: 'match_original', manual_gain_db: 0, eq_bands: [], compression: null, loudness: { profile: 'off' } })
})
it('blocks invalid manual gain without publishing it as a valid setting', async () => {
  const { node, model, valid } = await mount({ vocal_level: 'manual', manual_gain_db: 0 })
  const gain = input(node, 'Vocal gain (dB)'); gain.value = '15'; gain.dispatchEvent(new Event('input')); await settle()
  expect(valid.value).toBe(false); expect(model.value?.manual_gain_db).toBe(0)
  gain.value = '3'; gain.dispatchEvent(new Event('input')); await settle()
  expect(valid.value).toBe(true); expect(model.value?.manual_gain_db).toBe(3)
})
it('enforces the aggregate EQ boost limit and preserves unrelated invalid fields', async () => {
  const { node, valid } = await mount({ vocal_level: 'manual', eq_bands: [{ frequency_hz: 1000, gain_db: 4 }, { frequency_hz: 3000, gain_db: 4 }] })
  expect(valid.value).toBe(false)
  const remove = [...node.querySelectorAll('button')].find(item => item.textContent === 'Remove EQ band'); remove?.click(); await settle()
  expect(valid.value).toBe(true)
  const gain = input(node, 'Vocal gain (dB)'); gain.value = ''; gain.dispatchEvent(new Event('input')); await settle()
  input(node, 'Compress vocal dynamics').click(); await settle()
  expect(valid.value).toBe(false)
})
