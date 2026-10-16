// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import DatasetLibrary from './DatasetLibrary.vue'
import { i18n } from '../../i18n'
import type { LoraDatasetIssue } from '../../api/generated'

let app: App | undefined
afterEach(() => { app?.unmount(); document.body.replaceChildren() })
const dataset = { id: 'dataset-one', name: 'Saved voice', dataset_path: 'datasets/one/data.json', audio_dir: 'datasets/one', sample_count: 22, labeled_count: 17, revision: 1, trashed: false, available: true }
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

function button(label: string): HTMLButtonElement {
  const result = [...document.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!result) throw new Error(`Missing button: ${label}`)
  return result
}
async function mountTrash() {
  const removed: string[][] = []
  const props = reactive({ datasets: [dataset, { ...dataset, id: 'dataset-two', name: 'Old voice', trashed: true }, { ...dataset, id: 'dataset-three', name: 'Old drums', trashed: true }], selectedId: dataset.id, disabled: false, loading: false, issues: [] as LoraDatasetIssue[], onRemoveRecords: (ids: string[]) => removed.push(ids) })
  app = createApp(defineComponent({ setup: () => () => h(DatasetLibrary, props) })).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await nextTick()
  return { props, removed }
}
it('cancels record removal without submitting and explains that files stay on disk', async () => {
  const { removed } = await mountTrash()
  expect(button('Remove record').getAttribute('aria-label')).toBe('Remove dataset record: Old voice')
  button('Remove record').click(); await nextTick()
  expect(document.body.textContent).toContain('Remove 1 trash record?')
  expect(document.body.textContent).toContain('audio, labels, tensors and adapters stay on disk')
  expect(document.body.textContent).toContain('cannot restore these records from Trash')
  expect(document.activeElement).toBe(button('Cancel'))
  button('Cancel').click(); await nextTick()
  expect(removed).toEqual([])
  expect(document.body.textContent).not.toContain('Remove 1 trash record?')
})
it('confirms only the named individual trash record', async () => {
  const { removed } = await mountTrash()
  button('Remove record').click(); await nextTick()
  const confirmation = document.querySelector('[data-testid="remove-records-confirmation"]')
  expect(confirmation?.textContent).toContain('Old voice')
  expect(confirmation?.textContent).not.toContain('Old drums')
  expect(removed).toEqual([])
  button('Remove records, keep files').click(); await nextTick()
  expect(removed).toEqual([['dataset-two']])
})
it('clears only the displayed trash records after a batch confirmation', async () => {
  const { removed } = await mountTrash()
  button('Clear trash records').click(); await nextTick()
  const confirmation = document.querySelector('[data-testid="remove-records-confirmation"]')
  expect(confirmation?.textContent).toContain('Remove 2 trash records?')
  expect(confirmation?.textContent).toContain('Old voice')
  expect(confirmation?.textContent).toContain('Old drums')
  expect(confirmation?.textContent).not.toContain('Saved voice')
  button('Remove records, keep files').click(); await nextTick()
  expect(removed).toEqual([['dataset-two', 'dataset-three']])
})
it('blocks removal while loading and abandons confirmation if a record changes', async () => {
  const { props, removed } = await mountTrash()
  button('Clear trash records').click(); await nextTick()
  props.loading = true; await nextTick()
  expect(button('Remove records, keep files').disabled).toBe(true)
  button('Remove records, keep files').click()
  expect(removed).toEqual([])
  props.loading = false
  props.datasets[1] = { ...dataset, id: 'dataset-two', name: 'Old voice', trashed: true, revision: 2 }
  await nextTick()
  expect(document.querySelector('[data-testid="remove-records-confirmation"]')).toBeNull()
  expect(removed).toEqual([])
})
it('shows preserved dataset issues with file names and safe translated reasons', async () => {
  const { props } = await mountTrash()
  props.issues = [{ path: 'datasets/old/dataset.json', code: 'dataset_unavailable' }, { path: 'datasets/bad/dataset.json', code: 'dataset_invalid' }, { path: 'datasets/link/dataset.json', code: 'dataset_path_unsafe' }]
  await nextTick()
  expect(document.body.textContent).toContain('3 dataset files could not be opened. Their files have been preserved.')
  expect(document.body.textContent).toContain('datasets/old/dataset.json')
  expect(document.body.textContent).toContain('The dataset file is missing or unavailable.')
  expect(document.body.textContent).toContain('The file is not a valid saved dataset.')
  expect(document.body.textContent).toContain('outside the allowed library')
})
