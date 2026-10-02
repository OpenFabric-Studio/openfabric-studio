// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import TagInput from './TagInput.vue'
import { i18n } from '../../i18n'

let app: App | undefined
const updates: string[] = []
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); updates.length = 0 })
function mount(value = '') {
  const container = document.body.appendChild(document.createElement('div'))
  app = createApp(TagInput, { modelValue: value, 'onUpdate:modelValue': (next: string) => updates.push(next) }).use(i18n)
  app.mount(container)
  const input = container.querySelector('input')
  if (!input) throw new Error('Input missing')
  return { input, container }
}
async function type(input: HTMLInputElement, value: string) { input.value = value; input.dispatchEvent(new Event('input')); await nextTick() }
it('commits unfinished comma-separated tags on blur with duplicate prevention', async () => {
  const { input } = mount('rock'); await type(input, ' ROCK, dreamy '); input.dispatchEvent(new Event('blur')); await nextTick()
  expect(updates).toEqual(['rock, dreamy'])
  expect(input.value).toBe('')
})
it('does not commit keyboard events while composing and commits the completed text after blur', async () => {
  const { input } = mount(); input.dispatchEvent(new CompositionEvent('compositionstart')); await type(input, '夢')
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, cancelable: true })); input.dispatchEvent(new Event('blur')); await nextTick()
  expect(updates).toEqual([])
  input.dispatchEvent(new CompositionEvent('compositionend')); await nextTick(); expect(updates).toEqual(['夢'])
})
it('selects a suggestion without first committing the partial draft', async () => {
  const { input, container } = mount(); input.dispatchEvent(new Event('focus')); await type(input, 'dream')
  const suggestion = container.querySelector('button'); if (!suggestion) throw new Error('Suggestion missing')
  suggestion.dispatchEvent(new MouseEvent('mousedown', { cancelable: true })); await nextTick()
  expect(updates).toHaveLength(1); expect(updates[0]).not.toBe('dream')
})
it('Escape dismisses suggestions and keeps the draft available for blur commit', async () => {
  const { input, container } = mount(); input.dispatchEvent(new Event('focus')); await type(input, 'dream')
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); await nextTick(); expect(container.querySelector('button')).toBeNull()
  input.dispatchEvent(new Event('blur')); await nextTick(); expect(updates).toEqual(['dream'])
})
it('clearing removes the pending draft as well as existing tags', async () => {
  const { input, container } = mount('rock'); await type(input, 'dream')
  const clear = container.querySelector('button[title]'); if (!clear) throw new Error('Clear missing')
  clear.dispatchEvent(new MouseEvent('mousedown', { cancelable: true })); input.dispatchEvent(new Event('blur')); await nextTick()
  expect(updates).toEqual([''])
})
