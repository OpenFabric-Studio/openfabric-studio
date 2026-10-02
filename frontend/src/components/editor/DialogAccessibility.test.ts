// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import LibraryPicker from './LibraryPicker.vue'
import EditorHelpModal from './EditorHelpModal.vue'
vi.mock('../../api/tracks', () => ({ listTracks: vi.fn().mockResolvedValue([]), uploadTrack: vi.fn() }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 5; i++) await nextTick() }
it('gives the library picker modal semantics, focused close and Escape restoration', async () => {
  const opener = document.body.appendChild(document.createElement('button')); opener.focus()
  const open = ref(true)
  app = createApp({ render: () => open.value ? h(LibraryPicker, { onClose: () => { open.value = false } }) : null }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await settle()
  const dialog = document.querySelector('[role=dialog][aria-modal=true]')
  expect(dialog).not.toBeNull()
  const close = dialog?.querySelector('button[aria-label]'); expect(document.activeElement).toBe(close)
  close?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await settle()
  expect(open.value).toBe(false); expect(document.activeElement).toBe(opener)
})
it('makes editor help a modal and ignores delayed focus after closing', async () => {
  const opener = document.body.appendChild(document.createElement('button')); opener.focus()
  const show = ref(false)
  app = createApp({ render: () => h(EditorHelpModal, { show: show.value, onClose: () => { show.value = false } }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await settle()
  show.value = true; await settle()
  const dialog = document.querySelector('[role=dialog][aria-modal=true]'); expect(dialog).not.toBeNull()
  show.value = false; await settle(); expect(document.activeElement).toBe(opener)
  show.value = true; await nextTick(); show.value = false; await settle()
  expect(document.activeElement).toBe(opener)
})
