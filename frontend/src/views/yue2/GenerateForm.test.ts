// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import GenerateForm from './GenerateForm.vue'
import { i18n, setLocale } from '../../i18n'
import type { JsonObject, ReferenceAbcResponse } from '../../api/contracts'
import * as references from '../../api/references'
import { ApiError } from '../../api/http'
import * as terms from '../../api/musicTerms'
vi.mock('../../api/musicTerms', () => ({ getMusicTerms: vi.fn(), acceptMusicTerms: vi.fn(), updateMusicTermsSettings: vi.fn() }))
beforeEach(() => { vi.mocked(terms.getMusicTerms).mockImplementation(async (engine, operation) => ({ engine, operation, revision: 0, noncommercial_enabled: false, requirements: [], allowed: true })) })
import { pendingMusicAdapter, pendingYueDraft, pendingYueReference } from '../../composables/generationDrafts'
const state = reactive<{ pendingParamsInsert: JsonObject | null; pendingAbcInsert: string | null }>({ pendingParamsInsert: null, pendingAbcInsert: null })
const generateBatch = vi.fn()
vi.mock('../../stores/yue2', () => ({ useYue2Store: () => ({ ...state, get pendingParamsInsert() { return state.pendingParamsInsert }, get pendingAbcInsert() { return state.pendingAbcInsert }, generateBatch, clearPendingParamsInsert: () => { state.pendingParamsInsert = null }, clearPendingAbcInsert: () => { state.pendingAbcInsert = null } }) }))
vi.mock('../../api/references', () => ({ transformAbc: vi.fn() }))
vi.mock('../../components/shared/VoiceSelect.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
vi.mock('../../api/musicLora', () => ({ listMusicAdapters: vi.fn(async () => ({ adapters: [{ id: 'a'.repeat(32), engine: 'yue2', target: 'ar', name: 'AR', sha256: 'c'.repeat(64), created_at: 'now', source_job_id: null }, { id: 'b'.repeat(32), engine: 'yue2', target: 'nar', name: 'NAR', sha256: 'd'.repeat(64), created_at: 'now', source_job_id: null }], history_incomplete: false })) }))
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear(); state.pendingParamsInsert = null; state.pendingAbcInsert = null; pendingMusicAdapter.value = null; pendingYueDraft.value = null; pendingYueReference.value = null; generateBatch.mockReset(); vi.mocked(references.transformAbc).mockReset() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount() { setLocale('en'); const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: GenerateForm }] }); await router.push('/'); const container = document.body.appendChild(document.createElement('div')); app = createApp(GenerateForm).use(i18n).use(router); app.mount(container); await settle(); return container }
function button(container: HTMLElement, label: string) { const found = [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
it('keeps new YuE generation blocked until explicit non-commercial acknowledgement', async () => {
  const before = { engine: 'yue2', operation: 'generation', revision: 0, noncommercial_enabled: false, allowed: false,
    requirements: [{ component_id: 'yue-weights', name: 'YuE weights', scope: 'model', restriction: 'noncommercial', source_url: 'https://example.org/terms', terms_digest: 'a'.repeat(64), acknowledged: false }] } satisfies Awaited<ReturnType<typeof terms.getMusicTerms>>
  vi.mocked(terms.getMusicTerms).mockResolvedValue(before)
  vi.mocked(terms.acceptMusicTerms).mockResolvedValue({ ...before, allowed: true, noncommercial_enabled: true, revision: 1, requirements: before.requirements.map(row => ({ ...row, acknowledged: true })) })
  const container = await mount()
  expect(button(container, '✨ Create music').disabled).toBe(true)
  const choices = container.querySelectorAll('section[aria-label="Publisher terms"] input')
  for (const choice of choices) { if (!(choice instanceof HTMLInputElement)) throw new Error('Missing terms choice'); choice.click() }
  await settle(); button(container, 'Accept reviewed terms').click(); await settle()
  expect(terms.acceptMusicTerms).toHaveBeenCalledWith(expect.objectContaining({ engine: 'yue2', operation: 'generation', allow_noncommercial: true }), expect.any(AbortSignal))
  expect(button(container, '✨ Create music').disabled).toBe(false)
  expect(generateBatch).not.toHaveBeenCalled()
})
it('applies trained acoustic adapter previews and clears both adapters for the base comparison', async () => {
  pendingMusicAdapter.value = { engine: 'yue2', target: 'nar', selection: { id: 'b'.repeat(32), strength: 1 } }
  const container = await mount()
  expect(generateBatch).not.toHaveBeenCalled()
  const lyrics = container.querySelector('#yue-lyrics')
  if (!(lyrics instanceof HTMLTextAreaElement)) throw new Error('Missing lyrics')
  lyrics.value = 'Words'; lyrics.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  const style = [...container.querySelectorAll('input')].find(input => input.placeholder === i18n.global.t('aceGen.stylePlaceholder'))
  if (!(style instanceof HTMLInputElement)) throw new Error('Missing style')
  style.value = 'Jazz'; style.dispatchEvent(new Event('input', { bubbles: true })); style.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: null, narAdapter: { id: 'b'.repeat(32), strength: 1 } }))
  pendingMusicAdapter.value = { engine: 'yue2', target: 'nar', selection: null }; await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: null, narAdapter: null }))
})
it('uses translated song title labels, placeholder and help text', async () => {
  const container = await mount()
  expect(container.querySelector('label[for="yue-song-title"]')?.textContent).toBe('Song title')
  expect(container.querySelector('#yue-song-title')?.getAttribute('aria-label')).toBe('Song title')
  expect(container.querySelector('#yue-song-title')?.getAttribute('placeholder')).toBe('Name this song')
  expect(container.textContent).toContain('Saved separately from the style tags.')
})
it('associates generation selects, sampling inputs and textareas with translated labels', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'melody' }; await settle()
  for (const field of container.querySelectorAll('select:not([name]), input[type="number"], textarea')) {
    if (!(field instanceof HTMLSelectElement || field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) throw new Error('Unexpected control')
    expect([...field.labels ?? []].some(label => !!label.textContent?.trim()), field.outerHTML).toBe(true)
  }
})
it('copying sparse parameters resets omitted steps, CFG and sampling values', async () => {
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'old', style: 'rock', cfg_scale: 4, num_inference_steps: 32, semantic_temperature: 0.8, abc_temperature: 0.7 }; await settle()
  state.pendingParamsInsert = { lyrics: 'new lyrics', style: 'jazz' }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ lyrics: 'new lyrics', style: 'jazz', options: { style: 'jazz', cot: 'off', num_inference_steps: 8 } }))
})
it('preserves deliberately absent steps and zero sampling while restoring a complete preset', async () => {
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', num_inference_steps: null, cfg_scale: null, semantic_temperature: 0 }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ options: { style: 'jazz', cot: 'off', semantic_temperature: 0 } }))
})
it('applies reviewed reference text without resetting the existing generator settings', async () => {
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'old lyrics', style: 'jazz', seed: 55, cot: 'off', num_inference_steps: 12 }; await settle()
  const { applyReferenceText } = await import('../../composables/generationDrafts')
  applyReferenceText('yue2', 'abc', 'X:1\nK:C\nC D E', 'a'.repeat(32)); await settle()
  applyReferenceText('yue2', 'lyrics', 'reviewed lyrics', 'a'.repeat(32)); await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ lyrics: 'reviewed lyrics', style: 'jazz', baseSeed: 55, cot: 'melody', options: { style: 'jazz', cot: 'melody', abc: 'X:1\nK:C\nC D E', num_inference_steps: 12 }, settings: expect.objectContaining({ referenceImportId: 'a'.repeat(32) }) }))
})
it('clears unrelated reference provenance when copying another track parameters', async () => {
  const container = await mount(); const { applyReferenceText } = await import('../../composables/generationDrafts')
  applyReferenceText('yue2', 'lyrics', 'reference lyrics', 'a'.repeat(32)); await settle()
  state.pendingParamsInsert = { lyrics: 'different lyrics', style: 'jazz' }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ settings: expect.objectContaining({ referenceImportId: null }) }))
})
it('restores independent managed adapters from copied parameters and resets them on sparse copies', async () => {
  const container = await mount()
  const ar = { id: 'a'.repeat(32), strength: 0.5 }, nar = { id: 'b'.repeat(32), strength: 0 }
  state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', ar_adapter: ar, nar_adapter: nar }; await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: ar, narAdapter: nar, settings: expect.objectContaining({ arAdapter: ar, narAdapter: nar }) }))
  state.pendingParamsInsert = { lyrics: 'plain', style: 'rock' }; await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: null, narAdapter: null, settings: expect.objectContaining({ arAdapter: null, narAdapter: null }) }))
})
it('rejects native paths and invalid strengths copied from track parameters', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', ar_adapter: { id: '/private/adapter.safetensors', strength: 1 }, nar_adapter: { id: 'b'.repeat(32), strength: 3 } }; await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ arAdapter: null, narAdapter: null }))
})
it('reapplies both adapters from a complete generation preset', async () => {
  const container = await mount()
  const { emptyYueSettings } = await import('../../composables/generationSnapshots')
  const { pendingYueDraft } = await import('../../composables/generationDrafts')
  const ar = { id: 'a'.repeat(32), strength: 0.25 }, nar = { id: 'b'.repeat(32), strength: 1.25 }
  pendingYueDraft.value = { ...emptyYueSettings(), lyrics: 'preset lyrics', style: 'folk', arAdapter: ar, narAdapter: nar }
  await settle(); button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ lyrics: 'preset lyrics', arAdapter: ar, narAdapter: nar, settings: expect.objectContaining({ arAdapter: ar, narAdapter: nar }) }))
})

it('blocks YuE generation while an adapter strength is invalid and resumes with the corrected value', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', ar_adapter: { id: 'a'.repeat(32), strength: 1 } }; await settle()
  const selector = container.querySelector('select[name="adapter-ar"]')
  const strength = selector?.closest('fieldset')?.querySelector('input')
  if (!(strength instanceof HTMLInputElement)) throw new Error('Missing AR strength')
  strength.value = '3'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  const create = button(container, '✨ Create music')
  expect(create.disabled).toBe(true)
  create.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
  expect(generateBatch).not.toHaveBeenCalled()
  strength.value = '0.4'; strength.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(create.disabled).toBe(false)
  create.click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ arAdapter: { id: 'a'.repeat(32), strength: 0.4 } }))
})

it('explains an unsupported runtime adapter format on direct submission without raw errors', async () => {
  generateBatch.mockRejectedValue(new ApiError('music_lora_runtime_unsupported', 409))
  const container = await mount(); state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz' }; await settle()
  button(container, '✨ Create music').click(); await settle()
  expect(container.textContent).toContain('Update the native engine before using this adapter format')
  expect(container.textContent).not.toContain('music_lora_runtime_unsupported')
})

it('fills both independent selectors from a trained pair and clears both for base comparison', async () => {
  const ar = { id: 'a'.repeat(32), strength: 1 }, nar = { id: 'b'.repeat(32), strength: 1 }
  pendingMusicAdapter.value = { engine: 'yue2', target: 'nar', selection: nar, pair: { ar, nar } }
  const container = await mount()
  expect(container.querySelector<HTMLSelectElement>('select[name="adapter-ar"]')?.value).toBe(ar.id)
  expect(container.querySelector<HTMLSelectElement>('select[name="adapter-nar"]')?.value).toBe(nar.id)
  expect(generateBatch).not.toHaveBeenCalled()
  state.pendingParamsInsert = { lyrics: 'lyrics', style: 'jazz', ar_adapter: ar, nar_adapter: nar }; await settle()
  pendingMusicAdapter.value = { engine: 'yue2', target: 'nar', selection: nar, pair: { ar, nar } }; await settle()
  expect(generateBatch).not.toHaveBeenCalled()
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: ar, narAdapter: nar }))
  pendingMusicAdapter.value = { engine: 'yue2', target: 'nar', selection: null, pair: { ar: null, nar: null } }; await settle()
  expect(generateBatch).toHaveBeenCalledTimes(1)
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ arAdapter: null, narAdapter: null }))
})

const originalAbc = 'X:1\nM:4/4\nL:1/4\nQ:1/4=90\nK:C\n"C"C D E F|'
const transformedAbc = 'X:1\nM:3/4\nL:1/4\nQ:1/4=120\nK:D\n"D"D E F G|'
const abcResult: ReferenceAbcResponse = { abc: transformedAbc, note_count: 4, min_midi: 62, max_midi: 67, applied_transpose_semitones: 2 }
function field(container: HTMLElement, selector: string) {
  const element = container.querySelector(selector)
  if (!(element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement)) throw new Error(`Missing ${selector}`)
  return element
}
async function change(container: HTMLElement, selector: string, value: string) {
  const element = field(container, selector)
  element.value = value
  element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  await settle()
}
async function musicalChoices(container: HTMLElement) {
  await change(container, '#yue-bpm', '120')
  await change(container, '#yue-key-scale', 'D')
  await change(container, '#yue-time-signature', '3/4')
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Uninitialized promise') }
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

it('sends optional musical guidance with original style and no invented external score in automatic modes', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz' }; await settle()
  await musicalChoices(container)
  for (const cot of ['off', 'melody', 'full']) {
    await change(container, '#yue-cot', cot)
    button(container, '✨ Create music').click(); await settle()
    expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ options: { style: 'Jazz', cot, num_inference_steps: 8, bpm: 120, key_scale: 'D', time_signature: '3/4' }, settings: expect.objectContaining({ abc: '', bpm: 120, keyScale: 'D', timeSignature: '3/4' }) }))
  }
  expect(container.textContent).toContain('guidance')
  expect(references.transformAbc).not.toHaveBeenCalled()
})

it('applies score edits explicitly, then generates from the returned editable ABC without guidance fields', async () => {
  vi.mocked(references.transformAbc).mockResolvedValue(abcResult)
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'melody', abc: originalAbc }; await settle()
  await musicalChoices(container)
  const create = button(container, '✨ Create music')
  expect(create.disabled).toBe(true)
  create.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
  expect(generateBatch).not.toHaveBeenCalled()
  expect(field(container, '#yue-abc').value).toBe(originalAbc)
  button(container, 'Apply to ABC').click(); await settle()
  expect(references.transformAbc).toHaveBeenCalledWith({ abc: originalAbc, tempo_bpm: 120, target_key: 'D', time_signature: '3/4' }, expect.any(AbortSignal))
  expect(field(container, '#yue-abc').value).toBe(transformedAbc)
  expect(container.querySelector('[role="status"]')?.textContent).toContain('Changes applied to ABC')
  for (const id of ['yue-bpm', 'yue-key-scale', 'yue-time-signature']) expect(field(container, `#${id}`).value).toBe('')
  expect(create.disabled).toBe(false)
  await change(container, '#yue-abc', transformedAbc + '\n% reviewed')
  expect(container.querySelector('[role="status"]')).toBeNull()
  create.click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ options: { style: 'Jazz', cot: 'melody', num_inference_steps: 8, abc: transformedAbc + '\n% reviewed' }, settings: expect.objectContaining({ bpm: null, keyScale: null, timeSignature: null, abc: transformedAbc + '\n% reviewed' }) }))
})

it.each([
  ['key_mode_mismatch', 'Transposition keeps the existing major or minor mode'],
  ['unsupported_abc', 'unsupported notation'],
  ['invalid_abc', 'Check the ABC notation'],
  ['private /tmp/backend/trace', 'Could not apply'],
])('preserves ABC and pending choices after %s without exposing backend details', async (code, message) => {
  vi.mocked(references.transformAbc).mockRejectedValue(new ApiError(code, 422))
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'full', abc: originalAbc }; await settle()
  await musicalChoices(container)
  button(container, 'Apply to ABC').click(); await settle()
  expect(field(container, '#yue-abc').value).toBe(originalAbc)
  expect(field(container, '#yue-key-scale').value).toBe('D')
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(message)
  if (code === 'unsupported_abc') {
    for (const limit of ['one voice', 'major or minor key', 'tuplets', 'multiple voices', 'decorations']) expect(container.querySelector('[role="alert"]')?.textContent).toContain(limit)
  }
  expect(container.textContent).not.toContain(code)
  expect(button(container, '✨ Create music').disabled).toBe(true)
})

it.each(['#yue-abc', '#yue-bpm', '#yue-key-scale', '#yue-time-signature', '#yue-cot'])('ignores and aborts a score response when %s changes during Apply', async selector => {
  const request = deferred<ReferenceAbcResponse>()
  vi.mocked(references.transformAbc).mockReturnValue(request.promise)
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'full', abc: originalAbc }; await settle()
  await musicalChoices(container)
  button(container, 'Apply to ABC').click(); await settle()
  expect(button(container, '✨ Create music').disabled).toBe(true)
  const signal = vi.mocked(references.transformAbc).mock.calls[0]?.[1]
  const values: Record<string, string> = { '#yue-abc': originalAbc + '\n% changed', '#yue-bpm': '140', '#yue-key-scale': 'G', '#yue-time-signature': '6/8', '#yue-cot': 'off' }
  await change(container, selector, values[selector] ?? '')
  expect(signal?.aborted).toBe(true)
  request.resolve(abcResult); await settle()
  if (selector === '#yue-cot') await change(container, '#yue-cot', 'full')
  expect(field(container, '#yue-abc').value).toBe(selector === '#yue-abc' ? originalAbc + '\n% changed' : originalAbc)
  expect(field(container, selector).value).toBe(selector === '#yue-cot' ? 'full' : values[selector])
})

it('aborts score transformation on unmount and does not change a fresh form after its response', async () => {
  const request = deferred<ReferenceAbcResponse>()
  vi.mocked(references.transformAbc).mockReturnValue(request.promise)
  let container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'melody', abc: originalAbc }; await settle()
  await musicalChoices(container)
  button(container, 'Apply to ABC').click(); await settle()
  const signal = vi.mocked(references.transformAbc).mock.calls[0]?.[1]
  app?.unmount(); app = undefined; document.body.replaceChildren()
  expect(signal?.aborted).toBe(true)
  container = await mount()
  state.pendingParamsInsert = { lyrics: 'new', style: 'folk', cot: 'melody', abc: originalAbc + '\n% fresh' }; await settle()
  request.resolve(abcResult); await settle()
  expect(field(container, '#yue-abc').value).toBe(originalAbc + '\n% fresh')
})

it.each(['19', '301', '120.5'])('blocks invalid tempo %s and allows a corrected optional tempo', async value => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz' }; await settle()
  await change(container, '#yue-bpm', value)
  expect(field(container, '#yue-bpm').getAttribute('aria-invalid')).toBe('true')
  expect(container.querySelector('#yue-bpm-error')?.textContent).toContain('whole number from 20 to 300')
  const create = button(container, '✨ Create music')
  expect(create.disabled).toBe(true)
  create.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
  expect(generateBatch).not.toHaveBeenCalled()
  await change(container, '#yue-bpm', '')
  expect(create.disabled).toBe(false)
  create.click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ options: { style: 'Jazz', cot: 'off', num_inference_steps: 8 } }))
})

it('restores complete musical presets and copied parameters while sparse or invalid copies reset choices', async () => {
  const container = await mount()
  const { emptyYueSettings } = await import('../../composables/generationSnapshots')
  pendingYueDraft.value = { ...emptyYueSettings(), lyrics: 'preset', style: 'folk', bpm: 125, keyScale: 'F#m', timeSignature: '6/8' }
  await settle()
  expect(field(container, '#yue-bpm').value).toBe('125')
  expect(field(container, '#yue-key-scale').value).toBe('F#m')
  expect(field(container, '#yue-time-signature').value).toBe('6/8')
  state.pendingParamsInsert = { lyrics: 'copy', style: 'Jazz', bpm: 150, key_scale: 'Bb', time_signature: '3/4' }; await settle()
  expect(field(container, '#yue-bpm').value).toBe('150')
  expect(field(container, '#yue-key-scale').value).toBe('Bb')
  expect(field(container, '#yue-time-signature').value).toBe('3/4')
  const invalidCopies: JsonObject[] = [{}, { bpm: 999, key_scale: 'C dorian', time_signature: '7/8' }]
  for (const extra of invalidCopies) {
    state.pendingParamsInsert = { lyrics: 'plain', style: 'Jazz', ...extra }; await settle()
    button(container, '✨ Create music').click(); await settle()
    expect(generateBatch).toHaveBeenLastCalledWith(expect.objectContaining({ settings: expect.objectContaining({ bpm: null, keyScale: null, timeSignature: null }) }))
  }
})

it('cancels old score edits when reviewed ABC or copied parameters replace the draft', async () => {
  for (const source of ['reference', 'copy']) {
    const request = deferred<ReferenceAbcResponse>()
    vi.mocked(references.transformAbc).mockReturnValue(request.promise)
    const container = await mount()
    state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'full', abc: originalAbc }; await settle()
    await musicalChoices(container)
    button(container, 'Apply to ABC').click(); await settle()
    if (source === 'reference') pendingYueReference.value = { abc: originalAbc + '\n% imported', referenceImportId: null }
    else state.pendingParamsInsert = { lyrics: 'new', style: 'folk', cot: 'full', abc: originalAbc + '\n% imported' }
    await settle()
    request.resolve(abcResult); await settle()
    expect(field(container, '#yue-abc').value).toBe(originalAbc + '\n% imported')
    app?.unmount(); app = undefined; document.body.replaceChildren()
  }
})

it('provides translated visible labels, optional defaults and accessible musical help', async () => {
  const container = await mount()
  for (const id of ['yue-bpm', 'yue-key-scale', 'yue-time-signature']) {
    expect(container.querySelector(`label[for="${id}"]`)?.textContent?.trim()).toBeTruthy()
    expect(field(container, `#${id}`).value).toBe('')
  }
  expect(field(container, '#yue-bpm').getAttribute('min')).toBe('20')
  expect(field(container, '#yue-bpm').getAttribute('max')).toBe('300')
  button(container, 'About musical settings').click(); await settle()
  const dialog = container.querySelector('[role="dialog"]')
  expect(dialog?.getAttribute('aria-label')).toBe('Musical settings')
  for (const text of ['quarter-note', 'major or minor', 'does not rearrange', 'may not follow', 'one voice', 'tuplets', 'multiple voices', 'decorations']) expect(dialog?.textContent).toContain(text)
  expect(dialog?.textContent).not.toContain('yueScore.')
})

it('treats browser-invalid numeric text as an error instead of an omitted tempo', async () => {
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz' }; await settle()
  const input = field(container, '#yue-bpm')
  if (!(input instanceof HTMLInputElement)) throw new Error('Missing tempo input')
  Object.defineProperty(input.validity, 'badInput', { configurable: true, get: () => true })
  input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(input.getAttribute('aria-invalid')).toBe('true')
  expect(button(container, '✨ Create music').disabled).toBe(true)
  Object.defineProperty(input.validity, 'badInput', { configurable: true, get: () => false })
  await change(container, '#yue-bpm', '20')
  expect(button(container, '✨ Create music').disabled).toBe(false)
  button(container, '✨ Create music').click(); await settle()
  expect(generateBatch).toHaveBeenCalledWith(expect.objectContaining({ options: expect.objectContaining({ bpm: 20 }) }))
})

it('clears the applied notice when settings change, an Apply fails or a new score is imported', async () => {
  vi.mocked(references.transformAbc).mockResolvedValue(abcResult)
  const container = await mount()
  state.pendingParamsInsert = { lyrics: 'Words', style: 'Jazz', cot: 'melody', abc: originalAbc }; await settle()
  await musicalChoices(container)
  button(container, 'Apply to ABC').click(); await settle()
  expect(container.querySelector('[role="status"]')?.textContent).toContain('The controls now keep the score’s values.')
  await change(container, '#yue-bpm', '140')
  expect(container.querySelector('[role="status"]')).toBeNull()
  vi.mocked(references.transformAbc).mockRejectedValueOnce(new ApiError('unsupported_abc', 422))
  button(container, 'Apply to ABC').click(); await settle()
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('unsupported notation')
  expect(container.querySelector('[role="status"]')).toBeNull()
  button(container, 'Apply to ABC').click(); await settle()
  expect(container.querySelector('[role="status"]')?.textContent).toContain('Changes applied to ABC')
  pendingYueReference.value = { abc: originalAbc, referenceImportId: null }; await settle()
  expect(container.querySelector('[role="status"]')).toBeNull()
})
