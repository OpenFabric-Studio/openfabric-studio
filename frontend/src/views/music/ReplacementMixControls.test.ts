// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import type { ReplacementMixSettings } from '../../api/generated'
import ReplacementMixControls from './ReplacementMixControls.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let n = 0; n < 6; n++) await nextTick() }
it('reuses vocal controls while keeping master targets and the 3 dB makeup bound explicit', async () => {
  const model = ref<ReplacementMixSettings>({ master_level: 'off', loudness: { profile: 'off' }, space: 'off' }), valid = ref(true)
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(ReplacementMixControls, { modelValue: model.value, 'onUpdate:modelValue': value => { model.value = value }, onValid: value => { valid.value = value } }) }).use(i18n); app.mount(node); await settle()
  const checkbox = node.querySelector<HTMLInputElement>('input[type="checkbox"]'); checkbox?.click(); await settle()
  expect(model.value.compression?.makeup_db).toBe(0)
  const makeup = [...node.querySelectorAll<HTMLInputElement>('input')].find(item => item.parentElement?.textContent?.trim() === 'Makeup gain (dB)')
  expect(makeup?.max).toBe('3')
  if (!makeup) throw new Error('Missing makeup control')
  makeup.value = '4'; makeup.dispatchEvent(new Event('input')); await settle()
  expect(valid.value).toBe(false); expect(model.value.compression?.makeup_db).toBe(0)
  const master = [...node.querySelectorAll('select')].find(item => item.parentElement?.textContent?.includes('Final mix level'))
  if (!master) throw new Error('Missing master control')
  master.value = 'custom'; master.dispatchEvent(new Event('change')); await settle()
  expect(model.value.master_level).toBe('custom'); expect(model.value.loudness?.profile).toBe('music')
  master.value = 'off'; master.dispatchEvent(new Event('change')); await settle()
  expect(model.value.loudness?.profile).toBe('off')
})
