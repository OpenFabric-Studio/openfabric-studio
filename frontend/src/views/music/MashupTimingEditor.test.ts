// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import { mashupState } from '../../stores/mashupTestFixtures'
import type { MashupTiming } from '../../api/generated'
import MashupTimingEditor from './MashupTimingEditor.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('requires explicit grid confirmation and saves frame-exact manual sections without mutating its source', async () => {
  const source = mashupState().source_a, saved: MashupTiming[] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp(MashupTimingEditor, { source, onSave: (value: MashupTiming) => saved.push(value) }).use(i18n); app.mount(node); await nextTick()
  const save = node.querySelector<HTMLButtonElement>('button[type="submit"]')
  expect(save?.disabled).toBe(true)
  const count = node.querySelector<HTMLInputElement>('[data-bars]'); if (!count) throw new Error('Missing bars')
  count.value = '8'; count.dispatchEvent(new Event('input')); await nextTick()
  node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await nextTick()
  node.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await nextTick()
  expect(saved).toHaveLength(1); expect(saved[0]?.bpm).toBe(240)
  expect(saved[0]?.downbeat_frames).toEqual([0, 48000, 96000, 144000, 192000, 240000, 288000, 336000, 384000])
  expect(saved[0]?.sections.at(-1)?.end_frame).toBe(384000); expect(source.timing?.bpm).toBe(120)
})
it('blocks invalid section counts and does not manufacture a key estimate', async () => {
  const saved: MashupTiming[] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp(MashupTimingEditor, { source: mashupState().source_a, onSave: (value: MashupTiming) => saved.push(value) }).use(i18n); app.mount(node); await nextTick()
  const count = node.querySelector<HTMLInputElement>('[data-bars]'); if (!count) throw new Error('Missing bars')
  count.value = '1'; count.dispatchEvent(new Event('input')); await nextTick()
  node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await nextTick()
  expect(node.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true)
  expect(saved).toHaveLength(0); expect(node.textContent).toContain('Unknown')
})
