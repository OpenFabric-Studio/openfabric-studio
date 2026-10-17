// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import { ApiError } from '../../api/http'
import * as api from '../../api/rvcModels'
import { parseRvcModelImport } from '../../api/generated'
import RvcModelLibrary from './RvcModelLibrary.vue'
vi.mock('../../api/rvcModels', () => ({ listRvcModels: vi.fn(), importRvcModel: vi.fn(), removeRvcModel: vi.fn() }))
let app: App | undefined
async function settle() { for (let n = 0; n < 10; n++) await nextTick() }
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.listRvcModels).mockResolvedValue({ models: [] }) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function mount() { const node = document.body.appendChild(document.createElement('div')); app = createApp(RvcModelLibrary, { modelValue: '', voices: [{ id: 'a'.repeat(32), name: 'Authorized fixture profile' }] }).use(i18n); app.mount(node); await settle(); return node }
function input(node: HTMLElement, name: string, value: string) { const field = node.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[name="${name}"]`); if (!field) throw new Error(`Missing ${name}`); field.value = value; field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? 'change' : 'input')); return field }
it('shows selected files and requires all three explicit trust acknowledgements', async () => {
  const node = await mount()
  input(node, 'voice', 'a'.repeat(32)); input(node, 'label', 'Local voice'); input(node, 'evidence', 'Private signed permission reference'); input(node, 'rights', 'I have permission to use these fixture weights for this test.')
  const checkpoint = node.querySelector<HTMLInputElement>('input[name="checkpoint"]'); if (!checkpoint) throw new Error('Missing checkpoint')
  Object.defineProperty(checkpoint, 'files', { value: [new File(['opaque'], 'reviewed.pth')] }); checkpoint.dispatchEvent(new Event('change')); await settle()
  expect(node.textContent).toContain('reviewed.pth')
  const submit = node.querySelector<HTMLButtonElement>('button[type="submit"]'); expect(submit?.disabled).toBe(true)
  for (const checkbox of node.querySelectorAll<HTMLInputElement>('[data-trust]')) checkbox.click(); await settle()
  expect(submit?.disabled).toBe(false); expect(api.importRvcModel).not.toHaveBeenCalled()
})
it('does not treat missing runtime or model readiness as a usable registered voice', async () => {
  const validity: boolean[] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp(RvcModelLibrary, { modelValue: 'b'.repeat(32), voices: [], onValid: (value: boolean) => validity.push(value) }).use(i18n); app.mount(node); await settle()
  expect(validity.at(-1)).toBe(false); expect(api.importRvcModel).not.toHaveBeenCalled()
})
it.each([new Error('connection lost'), new ApiError('Invalid response from server', 200), new ApiError('Request timeout', 408)])('retries an uncertain import with the same request ID and exact metadata (%s)', async failure => {
  vi.mocked(api.importRvcModel).mockRejectedValueOnce(failure).mockImplementation(async metadata => ({ ...parseRvcModelImport(metadata), id: 'b'.repeat(32), checkpoint_sha256: 'c'.repeat(64), created_at: '2026-10-17T12:00:00Z' }))
  const node = await mount()
  input(node, 'voice', 'a'.repeat(32)); input(node, 'label', 'Local voice'); input(node, 'evidence', 'Permission reference'); input(node, 'rights', 'Permission declaration for a synthetic fixture.')
  const checkpoint = node.querySelector<HTMLInputElement>('input[name="checkpoint"]'); if (!checkpoint) throw new Error('Missing checkpoint')
  Object.defineProperty(checkpoint, 'files', { value: [new File(['opaque'], 'reviewed.pth')] }); checkpoint.dispatchEvent(new Event('change'))
  for (const checkbox of node.querySelectorAll<HTMLInputElement>('[data-trust]')) checkbox.click(); await settle()
  node.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
  expect(node.textContent).toContain('same request ID'); expect(node.querySelector<HTMLInputElement>('input[name="label"]')?.disabled || node.querySelector('fieldset')?.disabled).toBe(true)
  node.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
  expect(api.importRvcModel).toHaveBeenCalledTimes(2)
  expect(vi.mocked(api.importRvcModel).mock.calls[0]?.[0]).toEqual(vi.mocked(api.importRvcModel).mock.calls[1]?.[0])
})

it('unlocks rejected import details after a definite permission error', async () => {
  vi.mocked(api.importRvcModel).mockRejectedValueOnce(new ApiError('voice_consent_required', 403))
  const node = await mount()
  input(node, 'voice', 'a'.repeat(32)); input(node, 'label', 'Local voice'); input(node, 'evidence', 'Permission reference'); input(node, 'rights', 'Permission declaration for a synthetic fixture.')
  const checkpoint = node.querySelector<HTMLInputElement>('input[name="checkpoint"]'); if (!checkpoint) throw new Error('Missing checkpoint')
  Object.defineProperty(checkpoint, 'files', { value: [new File(['opaque'], 'reviewed.pth')] }); checkpoint.dispatchEvent(new Event('change'))
  for (const checkbox of node.querySelectorAll<HTMLInputElement>('[data-trust]')) checkbox.click(); await settle()
  node.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
  expect(node.querySelector('fieldset')?.disabled).toBe(false)
  input(node, 'rights', 'A corrected permission declaration for a synthetic fixture.'); await settle()
  expect(node.querySelector<HTMLButtonElement>('button[type="submit"]')?.textContent).toContain('Register trusted')
})
