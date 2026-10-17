// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import { mashupState } from '../../stores/mashupTestFixtures'
import type { MashupPlacement } from '../../api/generated'
import MashupPlanEditor from './MashupPlanEditor.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('requires a saved unlock before moving a locked placement and preserves the original plan', async () => {
  const state = mashupState(), plan = state.plan
  if (!plan || !plan.placements[0]) throw new Error('Missing fixture')
  plan.placements[0].locked = true
  const saved: MashupPlacement[][] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp(MashupPlanEditor, { plan, onSave: (value: MashupPlacement[]) => saved.push(value) }).use(i18n); app.mount(node); await nextTick()
  const first = node.querySelector('fieldset'), position = first?.querySelector<HTMLInputElement>('[data-start]'), lock = first?.querySelector<HTMLInputElement>('input[type="checkbox"]')
  expect(position?.disabled).toBe(true); lock?.click(); await nextTick(); expect(position?.disabled).toBe(true)
  node.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await nextTick()
  expect(saved[0]?.[0]).toMatchObject({ locked: false, target_start_tick: 0 }); expect(plan.placements[0].locked).toBe(true)
})
it('does not save invalid ranges and can reset edits', async () => {
  const plan = mashupState().plan, saved: MashupPlacement[][] = [], node = document.body.appendChild(document.createElement('div'))
  if (!plan) throw new Error('Missing fixture')
  app = createApp(MashupPlanEditor, { plan, onSave: (value: MashupPlacement[]) => saved.push(value) }).use(i18n); app.mount(node); await nextTick()
  const start = node.querySelector<HTMLInputElement>('[data-start]'); if (!start) throw new Error('Missing start')
  start.value = '999999'; start.dispatchEvent(new Event('input')); await nextTick()
  expect(node.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true)
  expect(saved).toHaveLength(0)
  ;[...node.querySelectorAll('button')].find(button => button.textContent === 'Reset to saved plan')?.click(); await nextTick()
  expect(start.value).toBe('0')
})
