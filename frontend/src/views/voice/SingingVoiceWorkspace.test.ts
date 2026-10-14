// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import SingingVoiceWorkspace from './SingingVoiceWorkspace.vue'
import * as api from '../../api/voices'
import * as productionApi from '../../api/artistProduction'
import { ApiError } from '../../api/http'
import { i18n, setLocale } from '../../i18n'
import { voiceProfile } from './voiceTestFixtures'

vi.mock('../../api/voices', async original => ({ ...await original<typeof import('../../api/voices')>(),
  listVoices: vi.fn(), uploadRecordings: vi.fn(),
}))
vi.mock('../../api/artistProduction', async original => ({ ...await original<typeof import('../../api/artistProduction')>(), planVoiceDeletion: vi.fn(), confirmVoiceDeletion: vi.fn() }))
vi.mock('./VoicePreparationReview.vue', () => ({ default: { template: '<section />' } }))
vi.mock('./VoiceComparisonLab.vue', () => ({ default: { template: '<section />' } }))
vi.mock('./LocalEnginePanel.vue', () => ({ default: { template: '<section />' } }))
vi.mock('./ArtistProductionPanel.vue', () => ({ default: {
  emits: ['labels'], template: `<button type="button" @click="$emit('labels', { language: 'es', register: 'low' })">Label next captures</button>`,
} }))

const first = voiceProfile()
const second = { ...voiceProfile('2'.repeat(32)), name: 'Second voice' }
let app: App | undefined
beforeEach(() => {
  vi.resetAllMocks(); setLocale('en')
  vi.mocked(api.listVoices).mockResolvedValue([first, second])
  vi.mocked(api.uploadRecordings).mockImplementation(async id => ({ voice: id === first.id ? first : second, saved: ['capture.wav'], skipped: [] }))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks() })
async function settle() { for (let index = 0; index < 10; index++) { await Promise.resolve(); await nextTick() } }
async function mount() {
  app = createApp(SingingVoiceWorkspace)
  app.use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] }))
  const container = document.body.appendChild(document.createElement('div'))
  app.mount(container); await settle(); return container
}
async function upload(container: HTMLElement) {
  const fileInput = container.querySelector('input[type=file]')
  if (!(fileInput instanceof HTMLInputElement)) throw new Error('Missing recording upload')
  const files = new DataTransfer(); files.items.add(new File(['temporary fixture'], 'capture.wav', { type: 'audio/wav' }))
  fileInput.files = files.files; fileInput.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!found) throw new Error(`Missing fixture button: ${label}`)
  return found
}
it('clears capture labels when selecting another voice and keeps labels on the owning upload', async () => {
  const container = await mount()
  button(container, 'Label next captures').click(); await settle()
  await upload(container)
  expect(api.uploadRecordings).toHaveBeenLastCalledWith(first.id, expect.any(Array), expect.any(AbortSignal), { language: 'es', register: 'low' })
  const selectSecond = [...container.querySelectorAll('button')].find(item => item.textContent?.includes(second.name))
  if (!selectSecond) throw new Error('Missing second voice')
  selectSecond.click(); await settle()
  await upload(container)
  expect(api.uploadRecordings).toHaveBeenLastCalledWith(second.id, expect.any(Array), expect.any(AbortSignal))
})
it('describes deletion inventory decisions and pending copies while keeping relative paths visible', async () => {
  vi.mocked(productionApi.planVoiceDeletion).mockResolvedValue({
    id: 'a'.repeat(32), voice_id: first.id, created_at: 'fixture', status: 'pending', plan_sha256: 'b'.repeat(64), items: [
      { id: 'working-copy', kind: 'working_copy', root_alias: 'library_voice_work', relative_path: '_apply/work_1/source_mono.wav', action: 'unknown', status: 'pending', error_code: 'voice_retention_copy_review_required' },
      { id: 'source-copy', kind: 'source_asset', root_alias: 'library_files', relative_path: 'artist-production/sources/fixture.wav', action: 'retain', status: 'retained' },
      { id: 'recording', kind: 'recording', root_alias: 'library_voices', relative_path: `${first.id}/recordings/take.wav`, action: 'delete', status: 'planned' },
    ],
  })
  const container = await mount()
  button(container, 'Delete voice').click(); await settle()
  const confirmation = document.querySelector('[data-confirm-delete]')
  if (!(confirmation instanceof HTMLButtonElement)) throw new Error('Missing deletion confirmation')
  confirmation.click(); await settle()
  const inventory = document.querySelector('[aria-label="Reviewed deletion inventory"]')
  expect(inventory?.textContent).toContain('Processing working copy')
  expect(inventory?.textContent).toContain('Voice processing workspace')
  expect(inventory?.textContent).toContain('Review ownership and retention')
  expect(inventory?.textContent).toContain('Production source or derived asset')
  expect(inventory?.textContent).toContain('_apply/work_1/source_mono.wav')
  expect(inventory?.textContent).toContain('artist-production/sources/fixture.wav')
  expect(inventory?.textContent).toContain('This processing copy needs a separate retention decision; the voice deletion confirmation leaves it intact.')
  expect(inventory?.textContent).not.toContain('voice_retention_copy_review_required')
})
it('keeps the reviewed inventory and voice visible when receipt capacity blocks deletion', async () => {
  vi.mocked(productionApi.planVoiceDeletion).mockResolvedValue({
    id: 'a'.repeat(32), voice_id: first.id, created_at: 'fixture', status: 'pending', plan_sha256: 'b'.repeat(64), items: [],
  })
  vi.mocked(productionApi.confirmVoiceDeletion).mockRejectedValue(new ApiError('voice_retention_receipt_capacity_exceeded', 409))
  const container = await mount()
  button(container, 'Delete voice').click(); await settle()
  const confirmation = document.querySelector('[data-confirm-delete]')
  if (!(confirmation instanceof HTMLButtonElement)) throw new Error('Missing deletion confirmation')
  confirmation.click(); await settle()
  confirmation.click(); await settle()
  expect(document.body.textContent).toContain('Receipt history reached its safety limit. No files were removed in this attempt.')
  expect(document.body.textContent).not.toContain('voice_retention_receipt_capacity_exceeded')
  expect(document.querySelector('[data-confirm-delete]')).toBe(confirmation)
  expect(container.textContent).toContain(first.name)
})
