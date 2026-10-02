// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n, setLocale } from '../../i18n'
import AppFooter from './AppFooter.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('opens attributed license sources in a focused dialog and returns focus on Escape', async () => {
  setLocale('en'); const node = document.body.appendChild(document.createElement('div'))
  app = createApp(AppFooter).use(i18n); app.mount(node)
  const opener = node.querySelector('button'); if (!opener) throw new Error('Missing notices button')
  opener.focus(); opener.click(); for (let i = 0; i < 4; i++) await nextTick()
  const dialog = document.querySelector('[role=dialog]'); if (!dialog) throw new Error('Missing notices dialog')
  expect(dialog.contains(document.activeElement)).toBe(true)
  const links = [...dialog.querySelectorAll('a')]
  expect(links).toHaveLength(5); expect(links.every(link => link.rel === 'noopener')).toBe(true)
  expect(links.map(link => link.href)).toContain('https://github.com/inikolax/remiqora')
  expect(links.map(link => link.href)).toContain('https://github.com/mchosc/remiqora')
  dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
  for (let i = 0; i < 4; i++) await nextTick()
  expect(document.querySelector('[role=dialog]')).toBeNull(); expect(document.activeElement).toBe(opener)
})
