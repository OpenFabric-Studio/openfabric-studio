// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import DatasetLibrary from './DatasetLibrary.vue'
import { i18n } from '../../i18n'

let app: App | undefined
afterEach(() => { app?.unmount(); document.body.replaceChildren() })
const dataset = { id: 'dataset-one', name: 'Saved voice', dataset_path: 'datasets/one/data.json', audio_dir: 'datasets/one', sample_count: 22, labeled_count: 17, revision: 1, trashed: false, available: true, error_code: null }
async function mount(disabled = false) {
  const events: string[] = []
  app = createApp(DatasetLibrary, { datasets: [dataset, { ...dataset, id: 'dataset-two', name: 'Old voice', trashed: true }], selectedId: dataset.id, disabled, loading: false, onSelect: (id: string) => events.push(id), onCreate: (name: string) => events.push(name), onRestore: (id: string) => events.push(id) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await nextTick(); return events
}
it('lists saved datasets with counts and a keyboard accessible selector', async () => {
  await mount()
  expect(document.querySelector('select')?.getAttribute('aria-label')).toBe('Saved dataset')
  expect(document.body.textContent).toContain('22 samples · 17 labelled')
  expect(document.querySelectorAll('select option')).toHaveLength(2)
})
it('creates a dataset through an inline name form', async () => {
  const events = await mount()
  const button = [...document.querySelectorAll('button')].find(item => item.textContent?.trim() === 'New')
  button?.click(); await nextTick()
  const input = document.querySelector('input')
  if (!(input instanceof HTMLInputElement)) throw new Error('Missing name field')
  input.value = 'New voice'; input.dispatchEvent(new Event('input')); await nextTick()
  document.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true })); await nextTick()
  expect(events).toEqual(['New voice'])
})
it('keeps trash recoverable and blocks conflicting controls during work', async () => {
  await mount(true)
  expect(document.querySelector('select')?.disabled).toBe(true)
  expect([...document.querySelectorAll('button')].every(button => button.disabled)).toBe(true)
  expect(document.body.textContent).toContain('Old voice')
  expect(document.body.textContent).toContain('Restore')
})
