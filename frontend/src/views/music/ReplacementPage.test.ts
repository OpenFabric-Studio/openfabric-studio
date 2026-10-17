// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { i18n } from '../../i18n'
import * as api from '../../api/replacement'
import * as voiceApi from '../../api/voices'
import { replacementCapabilities, replacementPlan } from '../../stores/replacementTestFixtures'
import { parseAlignmentResult } from '../../api/generated'
import ReplacementPage from './ReplacementPage.vue'
import { replacementDiagnosticsFixture } from './replacementDiagnosticsTestFixtures'
vi.mock('../../api/replacement', async original => ({ ...await original<typeof import('../../api/replacement')>(), listReplacementPlans: vi.fn(), replacementCapabilities: vi.fn(), getReplacementPlan: vi.fn(), createReplacementPlan: vi.fn(), previewReplacementStorage: vi.fn(), prepareReplacementPlan: vi.fn(), cancelReplacementPlan: vi.fn(), editReplacementMix: vi.fn(), editReplacementAlignment: vi.fn(), updateReplacementPlan: vi.fn() }))
vi.mock('../../api/voices', () => ({ listVoices: vi.fn() }))
vi.mock('../../components/shared/MusicSourcePicker.vue', () => ({ default: defineComponent({ props: ['label'], emits: ['update:modelValue'], setup: (props, { emit }) => () => h('button', { type: 'button', onClick: () => { const request = replacementPlan().request; if (request.mode !== 'prepared') throw new Error('Missing prepared fixture'); emit('update:modelValue', props.label === 'Original song' ? request.source : request.prepared) } }, props.label) }) }))
let app: App | undefined
async function settle() { for (let n = 0; n < 20; n++) await nextTick() }
function button(node: HTMLElement, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
async function mount() { const node = document.body.appendChild(document.createElement('div')); app = createApp(ReplacementPage).use(createPinia()).use(i18n); app.component('RouterLink', { props: ['to'], template: '<a :href="to"><slot/></a>' }); app.mount(node); await settle(); return node }
beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear(); vi.useFakeTimers()
  vi.mocked(api.listReplacementPlans).mockResolvedValue({ plans: [] }); vi.mocked(api.replacementCapabilities).mockResolvedValue(replacementCapabilities()); vi.mocked(voiceApi.listVoices).mockResolvedValue([])
  vi.mocked(api.previewReplacementStorage).mockResolvedValue({ source_bytes: 100, retained_bytes: 100, temporary_bytes: 1000, required_free_bytes: 2000, free_bytes: 4000, can_start: true })
  vi.mocked(api.createReplacementPlan).mockImplementation(async request => ({ ...replacementPlan(), request }))
  vi.mocked(api.prepareReplacementPlan).mockResolvedValue({ ...replacementPlan(), status: 'running', phase: 'aligning' })
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
it('saves a no-voice prepared workflow after storage review and starts only on Prepare', async () => {
  const node = await mount(), name = node.querySelector<HTMLInputElement>('input[maxlength="300"]')
  if (!name) throw new Error('Missing name')
  name.value = 'Prepared fixture'; name.dispatchEvent(new Event('input')); button(node, 'Original song').click(); await settle(); button(node, 'Continue').click(); await settle()
  button(node, 'Prepared vocal recording').click(); await settle()
  expect(api.createReplacementPlan).not.toHaveBeenCalled(); button(node, 'Review source and disk space').click(); await settle()
  expect(api.prepareReplacementPlan).not.toHaveBeenCalled(); button(node, 'Save replacement plan').click(); await settle()
  expect(api.createReplacementPlan).toHaveBeenCalledWith(expect.objectContaining({ mode: 'prepared' }), expect.any(AbortSignal), expect.any(String))
  expect(vi.mocked(api.createReplacementPlan).mock.calls[0]?.[0]).not.toHaveProperty('target')
  expect(api.prepareReplacementPlan).not.toHaveBeenCalled(); button(node, 'Prepare vocals and backing').click(); await settle()
  expect(api.prepareReplacementPlan).toHaveBeenCalledOnce(); expect(node.textContent).toContain('Proposing vocal timing')
  const reads = vi.mocked(api.getReplacementPlan).mock.calls.length
  app?.unmount(); app = undefined; expect(api.cancelReplacementPlan).not.toHaveBeenCalled()
  // Vue's devtools bootstrap timer may expire; workflow polling must not run.
  await vi.advanceTimersByTimeAsync(6000)
  expect(api.getReplacementPlan).toHaveBeenCalledTimes(reads); expect(vi.getTimerCount()).toBe(0)
})

it('preserves unsaved mix controls when the same plan revision is refreshed', async () => {
  const plan = replacementPlan()
  vi.mocked(api.listReplacementPlans).mockResolvedValue({ plans: [{ id: plan.id, revision: 1, mode: 'prepared', title: plan.request.title, status: 'draft', phase: 'inspecting', updated_at: plan.updated_at }] })
  vi.mocked(api.getReplacementPlan).mockImplementation(async () => replacementPlan())
  const node = await mount()
  const level = [...node.querySelectorAll<HTMLSelectElement>('select')].find(field => field.parentElement?.textContent?.startsWith('Vocal level'))
  if (!level) throw new Error('Missing vocal level')
  level.value = 'manual'; level.dispatchEvent(new Event('change')); await settle()
  const gain = [...node.querySelectorAll<HTMLInputElement>('input')].find(field => field.parentElement?.textContent?.trim() === 'Vocal gain (dB)')
  if (!gain) throw new Error('Missing vocal gain')
  gain.value = '-3'; gain.dispatchEvent(new Event('input')); await settle()
  button(node, 'Reload saved progress').click(); await settle()
  expect(Number(gain.value)).toBe(-3)
  expect(level.value).toBe('manual')
})

async function editMix(node: HTMLElement) {
  const level = [...node.querySelectorAll<HTMLSelectElement>('select')].find(field => field.parentElement?.textContent?.startsWith('Vocal level'))
  if (!level) throw new Error('Missing vocal level')
  level.value = 'manual'; level.dispatchEvent(new Event('change')); await settle()
  const gain = [...node.querySelectorAll<HTMLInputElement>('input')].find(field => field.parentElement?.textContent?.trim() === 'Vocal gain (dB)')
  if (!gain) throw new Error('Missing vocal gain')
  gain.value = '-3'; gain.dispatchEvent(new Event('input')); await settle(); return gain
}
function savedPlan() {
  const plan = replacementPlan()
  vi.mocked(api.listReplacementPlans).mockResolvedValue({ plans: [{ id: plan.id, revision: 1, mode: 'prepared', title: plan.request.title, status: 'draft', phase: 'inspecting', updated_at: plan.updated_at }] })
  vi.mocked(api.getReplacementPlan).mockResolvedValue(plan); return plan
}
it('preserves a mix draft across a remote revision and blocks saves until explicit review', async () => {
  const plan = savedPlan(), node = await mount(), gain = await editMix(node)
  vi.mocked(api.getReplacementPlan).mockResolvedValue({ ...plan, revision: 2 })
  button(node, 'Reload saved progress').click(); await settle()
  expect(Number(gain.value)).toBe(-3); expect(node.textContent).toContain('Your unsaved edits are preserved')
  expect(button(node, 'Save mix settings').disabled).toBe(true); expect(button(node, 'Render and save new mix').disabled).toBe(true)
  button(node, 'Review my edits against saved revision').click(); await settle()
  vi.mocked(api.editReplacementMix).mockResolvedValue({ ...plan, revision: 3, mix: { ...plan.mix, vocal_level: 'manual', manual_gain_db: -3 } })
  button(node, 'Save mix settings').click(); await settle()
  expect(api.editReplacementMix).toHaveBeenCalledWith(plan.id, expect.objectContaining({ expected_revision: 2, mix: expect.objectContaining({ manual_gain_db: -3 }) }), expect.any(AbortSignal))
})
it('accepts its own mix save without discarding an unrelated unsaved request', async () => {
  const plan = savedPlan(), node = await mount(); await editMix(node)
  const title = node.querySelector<HTMLInputElement>('input[maxlength="300"]'); if (!title) throw new Error('Missing title')
  title.value = 'Unsaved local title'; title.dispatchEvent(new Event('input')); await settle()
  vi.mocked(api.editReplacementMix).mockResolvedValue({ ...plan, revision: 2, mix: { ...plan.mix, vocal_level: 'manual', manual_gain_db: -3 } })
  button(node, 'Save mix settings').click(); await settle()
  expect(api.editReplacementMix).toHaveBeenCalledOnce(); expect(title.value).toBe('Unsaved local title'); expect(node.textContent).not.toContain('A newer saved revision arrived')
})
function alignmentPlan() {
  const plan = savedPlan(), request = plan.request; if (request.mode !== 'prepared') throw new Error('Missing fixture')
  const alignment = parseAlignmentResult({ id: 'f'.repeat(32), request: { reference: request.source.source, reference_sha256: request.source.audio_sha256, prepared: request.prepared.source, prepared_sha256: request.prepared.audio_sha256, revision: 1 }, status: 'needs_review', anchors: [{ source_sec: 0, target_sec: 0 }, { source_sec: 10, target_sec: 10 }], phrases: [{ phrase_id: '1'.repeat(32), reference_start_sec: 0, reference_end_sec: 2, prepared_start_sec: 0, prepared_end_sec: 2, reason: 'aligned' }], feature_method: 'synthetic-v1', matcher_method: 'fixture-v1', configuration_sha256: '2'.repeat(64), created_at: '2026-10-17T10:00:00Z' })
  const result = { ...plan, status: 'needs_review' as const, alignment }; vi.mocked(api.getReplacementPlan).mockResolvedValue(result); return result
}
async function editAnchor(node: HTMLElement) {
  const anchor = node.querySelector<HTMLInputElement>('.alignment-input'); if (!anchor) throw new Error('Missing alignment')
  anchor.value = '.1'; anchor.dispatchEvent(new Event('input')); node.querySelector<HTMLInputElement>('article input[type="checkbox"]')?.click(); await settle()
}
it('retains anchors and phrase review when its own mix save advances the plan revision', async () => {
  const plan = alignmentPlan(), node = await mount(); await editAnchor(node); await editMix(node)
  vi.mocked(api.editReplacementMix).mockResolvedValue({ ...plan, revision: 2, mix: { ...plan.mix, vocal_level: 'manual', manual_gain_db: -3 } })
  button(node, 'Save mix settings').click(); await settle()
  expect(Number(node.querySelector<HTMLInputElement>('.alignment-input')?.value)).toBe(.1); expect(node.querySelector<HTMLInputElement>('article input[type="checkbox"]')?.checked).toBe(true)
  expect(node.textContent).not.toContain('A newer saved revision arrived')
})
it('retains unsaved mix settings while accepting its own reviewed alignment', async () => {
  const plan = alignmentPlan(), node = await mount(); await editMix(node); await editAnchor(node)
  vi.mocked(api.editReplacementAlignment).mockImplementation(async (_id, request) => ({ ...plan, revision: 2, alignment: request.alignment, reviewed_phrase_ids: request.reviewed_phrase_ids, alignment_reviewed: true }))
  button(node, 'Save reviewed alignment').click(); await settle()
  expect(api.editReplacementAlignment).toHaveBeenCalledWith(plan.id, expect.objectContaining({ expected_revision: 1 }), expect.any(AbortSignal))
  expect([...node.querySelectorAll<HTMLInputElement>('input')].find(field => field.parentElement?.textContent?.trim() === 'Vocal gain (dB)')?.value).toBe('-3'); expect(node.textContent).not.toContain('A newer saved revision arrived')
})
it('retains unrelated mix edits after its own request save and canonical response', async () => {
  const plan = savedPlan(), node = await mount(); await editMix(node)
  const title = node.querySelector<HTMLInputElement>('input[maxlength="300"]'); if (!title) throw new Error('Missing title')
  title.value = 'New request title'; title.dispatchEvent(new Event('input')); await settle()
  vi.mocked(api.updateReplacementPlan).mockImplementation(async (_id, request) => ({ ...plan, revision: 2, request: request.request }))
  button(node, 'Review source and disk space').click(); await settle(); button(node, 'Save reviewed changes').click(); await settle()
  expect(api.updateReplacementPlan).toHaveBeenCalledOnce()
  expect([...node.querySelectorAll<HTMLInputElement>('input')].find(field => field.parentElement?.textContent?.trim() === 'Vocal gain (dB)')?.value).toBe('-3'); expect(node.textContent).not.toContain('A newer saved revision arrived')
})
it('discards drafts only after the explicit saved-revision choice', async () => {
  const plan = alignmentPlan(), node = await mount(); await editAnchor(node); await editMix(node)
  const remote = { ...plan, revision: 2, mix: { ...plan.mix, vocal_level: 'manual' as const, manual_gain_db: 2 }, alignment: { ...plan.alignment, anchors: [{ source_sec: 0, target_sec: 0 }, { source_sec: 9, target_sec: 10 }] } }
  vi.mocked(api.getReplacementPlan).mockResolvedValue(remote); button(node, 'Reload saved progress').click(); await settle()
  expect(Number(node.querySelector<HTMLInputElement>('.alignment-input')?.value)).toBe(.1); expect(button(node, 'Save reviewed alignment').disabled).toBe(true)
  button(node, 'Use saved revision').click(); await settle()
  expect(Number(node.querySelector<HTMLInputElement>('.alignment-input')?.value)).toBe(0); expect(node.querySelector<HTMLInputElement>('article input[type="checkbox"]')?.checked).toBe(false)
  expect([...node.querySelectorAll<HTMLInputElement>('input')].find(field => field.parentElement?.textContent?.trim() === 'Vocal gain (dB)')?.value).toBe('2')
  expect(api.editReplacementAlignment).not.toHaveBeenCalled(); expect(api.editReplacementMix).not.toHaveBeenCalled()
})

it('places a measured pitch suggestion into the draft without applying or saving it automatically', async () => {
  const plan = savedPlan()
  vi.mocked(api.getReplacementPlan).mockResolvedValue({ ...plan, status: 'needs_review', diagnostics: replacementDiagnosticsFixture() })
  const node = await mount()
  button(node, 'Use proposed pitch in settings draft').click(); await settle()
  const pitch = [...node.querySelectorAll<HTMLInputElement>('input')].find(field => field.parentElement?.textContent?.trim() === 'Pitch shift (semitones)')
  expect(pitch?.value).toBe('2')
  expect(api.updateReplacementPlan).not.toHaveBeenCalled(); expect(api.editReplacementMix).not.toHaveBeenCalled()
  expect(button(node, 'Use proposed EQ in mix draft').disabled).toBe(true)
})
it('saves reviewed EQ through the existing mix revision without changing source choices', async () => {
  const plan = savedPlan()
  vi.mocked(api.getReplacementPlan).mockResolvedValue({ ...plan, status: 'needs_review', diagnostics: replacementDiagnosticsFixture() })
  const node = await mount()
  button(node, 'Use proposed EQ in mix draft').click(); await settle()
  expect(api.editReplacementMix).not.toHaveBeenCalled()
  vi.mocked(api.editReplacementMix).mockImplementation(async (_id, request) => ({ ...plan, revision: 2, mix: request.mix }))
  button(node, 'Save mix settings').click(); await settle()
  expect(api.editReplacementMix).toHaveBeenCalledWith(plan.id, expect.objectContaining({ expected_revision: 1, mix: expect.objectContaining({eq_bands: [expect.objectContaining({ frequency_hz: 125, gain_db: 1 })]}) }), expect.any(AbortSignal))
  expect(api.updateReplacementPlan).not.toHaveBeenCalled()
})

it('offers analysis refresh after preparation and blocks it while timing edits are unsaved', async () => {
  alignmentPlan()
  const capabilities = replacementCapabilities()
  for (const option of capabilities.separation.options) option.available = true
  vi.mocked(api.replacementCapabilities).mockResolvedValue(capabilities)
  const node = await mount()
  expect(node.textContent).toContain('Unchanged recordings keep their reviewed anchors')
  expect(button(node, 'Refresh vocal analysis').disabled).toBe(false)
  await editAnchor(node)
  expect(button(node, 'Refresh vocal analysis').disabled).toBe(true)
})
