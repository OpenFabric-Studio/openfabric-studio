// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { i18n } from '../../i18n'
import * as api from '../../api/mashups'
import { mashupJob, mashupState } from '../../stores/mashupTestFixtures'
import MashupPage from './MashupPage.vue'
vi.mock('../../api/mashups', async original => ({ ...await original<typeof import('../../api/mashups')>(), listMashups: vi.fn(), getMashup: vi.fn(), listMashupJobs: vi.fn(), previewMashupSource: vi.fn(), createMashup: vi.fn(), previewMashupStorage: vi.fn(), startMashupRender: vi.fn(), publishMashupProject: vi.fn(), cancelMashup: vi.fn() }))
vi.mock('../../components/shared/MusicSourcePicker.vue', () => ({ default: defineComponent({
  props: ['label', 'disabled'], emits: ['update:modelValue', 'inspected', 'busy'],
  setup: (props, { emit }) => () => h('div', [
    h('button', { disabled: props.disabled, onClick: () => { const source = props.label === 'Source A' ? mashupState().source_a : mashupState().source_b; emit('update:modelValue', { source: source.input.source, audio_sha256: source.input.sha256 }); emit('inspected', { duration_sec: source.duration_sec }) } }, props.label),
    h('button', { onClick: () => emit('busy', true) }, `Load ${props.label}`),
    h('button', { onClick: () => emit('busy', false) }, `Finish ${props.label}`),
  ]),
}) }))
let app: App | undefined
async function settle() { for (let n = 0; n < 20; n++) await nextTick() }
function button(node: HTMLElement, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
async function mount() { const node = document.body.appendChild(document.createElement('div')); app = createApp(MashupPage).use(createPinia()).use(i18n); app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot/></a>' }); app.mount(node); await settle(); return node }
beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); vi.mocked(api.listMashups).mockResolvedValue({ documents: [] }); vi.mocked(api.getMashup).mockResolvedValue(mashupState()); vi.mocked(api.listMashupJobs).mockResolvedValue({ jobs: [] }); vi.mocked(api.previewMashupSource).mockImplementation(async request => ({ sha256: request.source.track_id === 1 ? '1'.repeat(64) : '2'.repeat(64), sample_rate_hz: 48000, channels: 2, duration_sec: request.source.track_id === 1 ? 8 : 6, source_bytes: 100, retained_bytes: 100, temporary_bytes: 100, required_free_bytes: 1000, free_bytes: 3000, can_start: true, separator_ready: true })); vi.mocked(api.createMashup).mockResolvedValue({ ...mashupState(), status: 'preparing' }); vi.mocked(api.previewMashupStorage).mockResolvedValue({ retained_bytes: 100, temporary_bytes: 100, required_free_bytes: 1000, free_bytes: 3000, can_start: true, frames: 384000, duration_sec: 8, stretch_ready: true }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
it('prepares independent sources only after a matching two-source review', async () => {
  const node = await mount(), title = node.querySelector<HTMLInputElement>('input[maxlength="120"]'); if (!title) throw new Error('Missing title')
  title.value = 'Two songs'; title.dispatchEvent(new Event('input')); button(node, 'Source A').click(); button(node, 'Source B').click(); await settle()
  expect(api.createMashup).not.toHaveBeenCalled(); expect(button(node, 'Prepare both songs').disabled).toBe(true)
  button(node, 'Review source and disk space').click(); await settle(); expect(api.previewMashupSource).toHaveBeenCalledTimes(2)
  button(node, 'Prepare both songs').click(); await settle()
  expect(api.createMashup).toHaveBeenCalledWith(expect.objectContaining({ source_a: expect.objectContaining({ sha256: '1'.repeat(64) }), source_b: expect.objectContaining({ sha256: '2'.repeat(64) }), separation_quality: 'fast' }), expect.any(AbortSignal))
  app?.unmount(); app = undefined; expect(api.cancelMashup).not.toHaveBeenCalled()
})
it('opens completed artifacts and prepares an editor without starting a render', async () => {
  vi.mocked(api.listMashups).mockResolvedValue({ documents: [mashupState()] }); vi.mocked(api.listMashupJobs).mockResolvedValue({ jobs: [mashupJob()] })
  vi.mocked(api.publishMashupProject).mockResolvedValue({ project_id: 27, provenance: { workflow: 'mashup', workflow_id: mashupState().id, workflow_revision: 1, source_manifest_sha256: 'f'.repeat(64) } })
  const node = await mount(); expect(api.startMashupRender).not.toHaveBeenCalled()
  expect(node.querySelector('audio')?.getAttribute('preload')).toBe('none')
  expect(node.textContent).toContain('Saved library track')
  const existing = mashupJob().manifest?.project_id
  if (existing) expect(node.querySelector(`a[href="/editor/${existing}"]`)).not.toBeNull()
  else { button(node, 'Prepare editor project').click(); await settle(); expect(node.querySelector('a[href="/editor/27"]')).not.toBeNull() }
})
it('refuses preparation when separate forecasts fit but their combined budget does not', async () => {
  vi.mocked(api.previewMashupSource).mockImplementation(async request => ({ sha256: request.source.track_id === 1 ? '1'.repeat(64) : '2'.repeat(64), sample_rate_hz: 48000, channels: 2, duration_sec: 8, source_bytes: 100, retained_bytes: 100, temporary_bytes: 100, required_free_bytes: 1000, free_bytes: 1500, can_start: true, separator_ready: true }))
  const node = await mount(), title = node.querySelector<HTMLInputElement>('input[maxlength="120"]'); if (!title) throw new Error('Missing title')
  title.value = 'Budget test'; title.dispatchEvent(new Event('input')); button(node, 'Source A').click(); button(node, 'Source B').click(); await settle()
  button(node, 'Review source and disk space').click(); await settle()
  expect(button(node, 'Prepare both songs').disabled).toBe(true); expect(api.createMashup).not.toHaveBeenCalled()
})
it('invalidates an approved render forecast after a musical setting changes', async () => {
  vi.mocked(api.listMashups).mockResolvedValue({ documents: [mashupState()] })
  const node = await mount(); button(node, 'Review render and disk space').click(); await settle()
  const confirmation = node.querySelector<HTMLInputElement>('[data-render-confirm]'); expect(confirmation).not.toBeNull()
  confirmation?.click(); await settle()
  const bpm = [...node.querySelectorAll<HTMLInputElement>('input')].find(input => input.parentElement?.textContent?.trim() === 'Output BPM')
  if (!bpm) throw new Error('Missing tempo')
  bpm.value = '125'; bpm.dispatchEvent(new Event('input')); await settle()
  expect(node.querySelector('[data-render-confirm]')).toBeNull(); expect(button(node, 'Review render and disk space').disabled).toBe(true)
  expect(api.startMashupRender).not.toHaveBeenCalled()
})

it('keeps the active source lookup enabled while it blocks conflicting page actions', async () => {
  const node = await mount()
  button(node, 'Load Source A').click(); await settle()
  expect(button(node, 'Source A').disabled).toBe(false)
  expect(button(node, 'Source B').disabled).toBe(true)
  expect(button(node, 'New mashup').disabled).toBe(true)
  button(node, 'Finish Source A').click(); await settle()
  expect(button(node, 'Source B').disabled).toBe(false)
})

it('keeps unsaved placements dirty after reloading the same saved plan revision', async () => {
  vi.mocked(api.listMashups).mockResolvedValue({ documents: [mashupState()] })
  const node = await mount()
  const gain = [...node.querySelectorAll<HTMLInputElement>('input[type="number"]')].find(field => field.parentElement?.textContent?.trim() === 'Gain (dB)')
  if (!gain) throw new Error('Missing placement gain')
  gain.value = '-3'; gain.dispatchEvent(new Event('input')); await settle()
  expect(button(node, 'Review render and disk space').disabled).toBe(true)
  vi.mocked(api.getMashup).mockResolvedValueOnce(mashupState())
  button(node, 'Reconnect').click(); await settle()
  expect(Number(gain.value)).toBe(-3)
  expect(node.textContent).toContain('Unsaved placement edits')
  expect(button(node, 'Review render and disk space').disabled).toBe(true)
  expect(api.startMashupRender).not.toHaveBeenCalled()
})
