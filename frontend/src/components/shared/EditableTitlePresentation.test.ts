// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import EditableTitle from './EditableTitle.vue'
import { i18n } from '../../i18n'
let app: App | undefined
afterEach(() => { app?.unmount(); document.body.replaceChildren() })
it('shows presentation text while preserving the complete title for editing and its tooltip', async () => {
 const full = 'iPhone song with a long descriptive title'; let renamed = ''
 app = createApp({ render: () => h(EditableTitle, { modelValue: full, displayText: 'iPhone song…', editable: true, onRename: value => { renamed = value } }) }).use(i18n)
 app.mount(document.body.appendChild(document.createElement('div')))
 expect(document.querySelector('p')?.textContent).toBe('iPhone song…')
 expect(document.querySelector('p')?.getAttribute('title')).toBe(full)
 document.querySelector('button')?.click(); await nextTick()
 const input = document.querySelector('input'); expect(input?.value).toBe(full)
 if (!input) throw new Error('Missing title editor')
 input.value = 'iPhone remix'; input.dispatchEvent(new Event('input')); input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })); await nextTick()
 expect(renamed).toBe('iPhone remix')
})
