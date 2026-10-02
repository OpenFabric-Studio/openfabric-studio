// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import HelpModal from './HelpModal.vue'
import { i18n } from '../../i18n'

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let index = 0; index < 4; index++) await nextTick() }
async function mount() {
  const opener = document.body.appendChild(document.createElement('button'))
  opener.textContent = 'Open help'; opener.focus()
  const open = ref(false)
  app = createApp({ setup: () => () => h(HelpModal, { open: open.value, title: 'Reference help', onClose: () => { open.value = false } }, {
    default: () => [h('button', { disabled: true }, 'Unavailable'), h('a', { href: '#details' }, 'More details')],
  }) }).use(i18n)
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  return { opener, open, container }
}
function closeButton(container: HTMLElement) {
  const button = container.querySelector('button[aria-label]')
  if (!(button instanceof HTMLButtonElement)) throw new Error('Missing close button')
  return button
}
function key(target: Element, key: string, shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true })
  target.dispatchEvent(event); return event
}

it('moves focus into help when opened and returns it on Escape or the close button', async () => {
  const { opener, open, container } = await mount()
  expect(document.activeElement).toBe(opener)
  open.value = true; await settle()
  expect(document.activeElement).toBe(closeButton(container))
  expect(key(closeButton(container), 'Escape').defaultPrevented).toBe(true); await settle()
  expect(open.value).toBe(false); expect(document.activeElement).toBe(opener)
  open.value = true; await settle(); closeButton(container).click(); await settle()
  expect(document.activeElement).toBe(opener)
})

it('wraps keyboard focus within help, skipping disabled controls', async () => {
  const { open, container } = await mount(); open.value = true; await settle()
  const first = closeButton(container)
  const last = container.querySelector('a')
  if (!(last instanceof HTMLAnchorElement)) throw new Error('Missing details link')
  expect(key(first, 'Tab', true).defaultPrevented).toBe(true); expect(document.activeElement).toBe(last)
  expect(key(last, 'Tab').defaultPrevented).toBe(true); expect(document.activeElement).toBe(first)
  expect(key(first, 'Tab').defaultPrevented).toBe(false)
})

it('recovers focus from outside an open dialog without intercepting closed-dialog keys', async () => {
  const { opener, open, container } = await mount()
  expect(key(opener, 'Tab').defaultPrevented).toBe(false)
  open.value = true; await settle(); opener.focus()
  expect(key(opener, 'Tab').defaultPrevented).toBe(true); expect(document.activeElement).toBe(closeButton(container))
  open.value = false; await settle()
  expect(key(opener, 'Escape').defaultPrevented).toBe(false)
})

it('restores connected opener focus and removes handlers when an open dialog unmounts', async () => {
  const { opener, open, container } = await mount(); open.value = true; await settle()
  expect(document.activeElement).toBe(closeButton(container))
  app?.unmount(); app = undefined; await settle()
  expect(document.activeElement).toBe(opener)
  expect(key(opener, 'Tab').defaultPrevented).toBe(false)
})

it('closes only the top nested dialog and returns focus to its opener', async () => {
  const outer = ref(true), inner = ref(false)
  app = createApp({ render: () => h('div', [
    h(HelpModal, { open: outer.value, title: 'Outer', onClose: () => { outer.value = false } }, {
      default: () => h('button', { id: 'inner-opener', onClick: () => { inner.value = true } }, 'More help'),
    }),
    h(HelpModal, { open: inner.value, title: 'Inner', onClose: () => { inner.value = false } }),
  ]) }).use(i18n)
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  const opener = container.querySelector<HTMLButtonElement>('#inner-opener')
  if (!opener) throw new Error('Missing nested opener')
  opener.focus(); opener.click(); await settle()
  const top = container.querySelector('[aria-label="Inner"] button')
  if (!top) throw new Error('Missing nested close')
  key(top, 'Escape'); await settle()
  expect(inner.value).toBe(false); expect(outer.value).toBe(true)
  expect(document.activeElement).toBe(opener)
})

it('skips controls hidden by an ancestor when wrapping focus', async () => {
  const open = ref(true)
  app = createApp({ render: () => h(HelpModal, { open: open.value, title: 'Hidden controls' }, {
    default: () => [h('a', { href: '#visible' }, 'Visible'), h('div', { style: { display: 'none' } }, h('button', {}, 'Hidden'))],
  }) }).use(i18n)
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); await settle()
  const last = container.querySelector('a')
  if (!last) throw new Error('Missing visible link')
  last.focus(); expect(key(last, 'Tab').defaultPrevented).toBe(true)
  expect(document.activeElement).toBe(closeButton(container))
})
