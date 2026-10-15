// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import * as api from '../../api/loraDataset'
import { parseLoraDatasetOperationResponse } from '../../api/generated'
import { savedTrainingSamples } from '../../composables/musicTrainingSamples'
import TrainingDatasetPicker from './TrainingDatasetPicker.vue'
vi.mock('../../api/loraDataset', () => ({ listLoraDatasets: vi.fn(), readSavedLoraDataset: vi.fn() }))
let app: App | undefined
const dataset = { id: 'a'.repeat(32), name: 'Old piano set', dataset_path: 'datasets/piano/dataset.json', audio_dir: 'datasets/piano', sample_count: 1, labeled_count: 1, revision: 7, available: true, trashed: false }
const snapshot = parseLoraDatasetOperationResponse({ dataset, samples: [{ id: 'old-clip', filename: 'piano.wav', caption: 'Warm piano', labeled: true, is_instrumental: true, lyrics: '[Instrumental]' }], metadata: {} })
async function settle() { for (let i = 0; i < 12; i++) await nextTick() }
function button(node: ParentNode, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.listLoraDatasets).mockResolvedValue({ datasets: [dataset], issues: [] }); vi.mocked(api.readSavedLoraDataset).mockResolvedValue(snapshot) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('selects a saved snapshot without loading or changing the native dataset builder', async () => {
  const selected = vi.fn()
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp(TrainingDatasetPicker, { limit: 100, onPick: selected }).use(i18n); app.mount(node)
  expect(api.listLoraDatasets).not.toHaveBeenCalled()
  button(node, 'Add a saved dataset').click(); await settle()
  expect(node.textContent).toContain('Old piano set')
  button(node, 'Add up to 1 recordings').click(); await settle()
  expect(api.readSavedLoraDataset).toHaveBeenCalledWith(dataset.id, 7, expect.any(AbortSignal))
  expect(selected).toHaveBeenCalledWith(snapshot)
})
it('keeps dataset identity and revision while separating model-specific captions and lyrics', () => {
  const samples = savedTrainingSamples(snapshot, 'heartmula', 100)
  expect(samples).toEqual([{ dataset: { dataset_id: dataset.id, revision: 7, sample_id: 'old-clip' }, title: 'piano.wav', caption: 'Warm piano', instrumental: true, lyrics: '' }])
  expect(savedTrainingSamples(snapshot, 'yue2', 0)).toEqual([])
})
