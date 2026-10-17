// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n, setLocale } from '../../i18n'
import * as api from '../../api/musicArtifacts'
import type { MusicArtifactCleanupPlan, MusicArtifactCleanupReceipt, MusicArtifactInventory } from '../../api/generated'
import MusicStorageReview from './MusicStorageReview.vue'

vi.mock('../../api/musicArtifacts', () => ({ getMusicArtifacts: vi.fn(), createMusicCleanupPlan: vi.fn(), getMusicCleanupPlan: vi.fn(), getMusicCleanupReceipt: vi.fn(), confirmMusicCleanupPlan: vi.fn() }))
const id = 'a'.repeat(32)
const plan: MusicArtifactCleanupPlan = { id: 'b'.repeat(32), created_at: '2026-10-17T12:00:00Z', plan_sha256: 'c'.repeat(64), status: 'pending', items: [{ job_id: id, disposition: 'eligible', bytes: 1024, source_sha256: 'd'.repeat(64) }] }
const inventory: MusicArtifactInventory = { generated_at: plan.created_at, scan_complete: false, groups: [
  { id: 'analysis:one', kind: 'analysis_workspace', root_alias: 'music_analysis', relative_path: id, disposition: 'review', dependencies: [{ kind: 'analysis_job', id }], size_bytes: 1024, size_complete: true },
  { id: 'adapter:one', kind: 'registered_adapter', root_alias: 'registered_adapters', disposition: 'retained', reasons: ['protected_adapter'] },
] }
const receipt: MusicArtifactCleanupReceipt = { plan_id: plan.id, plan_sha256: plan.plan_sha256, status: 'completed', items: [{ job_id: id, status: 'removed', removed_bytes: 1024 }] }
let app: App | undefined
beforeEach(() => {
  vi.clearAllMocks(); setLocale('en')
  vi.mocked(api.getMusicArtifacts).mockResolvedValue(inventory)
  vi.mocked(api.createMusicCleanupPlan).mockResolvedValue(plan)
  vi.mocked(api.confirmMusicCleanupPlan).mockResolvedValue(receipt)
  vi.mocked(api.getMusicCleanupPlan).mockResolvedValue(plan)
  vi.mocked(api.getMusicCleanupReceipt).mockResolvedValue(receipt)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
async function mount() { const node = document.body.appendChild(document.createElement('div')); app = createApp(MusicStorageReview).use(i18n); app.mount(node); await settle(); return node }
function button(node: HTMLElement, text: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === text); if (!found) throw new Error(`Missing ${text}`); return found }
async function open(node: HTMLElement) { button(node, 'Music storage review').click(); await settle() }
async function select(node: HTMLElement) { const checkbox = node.querySelector<HTMLInputElement>('input[type="checkbox"]'); if (!checkbox) throw new Error('Missing copy selection'); checkbox.click(); await settle() }
async function prepare(node: HTMLElement) { await select(node); button(node, 'Review selected copies').click(); await settle() }
async function confirmReview(node: HTMLElement) { const labels = [...node.querySelectorAll('label')]; const label = labels.find(item => item.textContent?.includes('I reviewed these copies')); label?.querySelector('input')?.click(); await settle() }

it('loads only after opening and never starts cleanup during storage review', async () => {
  const node = await mount(); expect(api.getMusicArtifacts).not.toHaveBeenCalled()
  await open(node); expect(api.getMusicArtifacts).toHaveBeenCalledTimes(1)
  expect(node.textContent).toContain('Some sizes or folders could not be checked')
  expect(node.querySelectorAll('input[type="checkbox"]')).toHaveLength(1)
  expect(api.createMusicCleanupPlan).not.toHaveBeenCalled(); expect(api.confirmMusicCleanupPlan).not.toHaveBeenCalled()
})
it('requires a selected-copy review followed by explicit confirmation', async () => {
  const node = await mount(); await open(node); await prepare(node)
  expect(api.createMusicCleanupPlan).toHaveBeenCalledWith({ analysis_job_ids: [id] }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Cleanup cannot be undone; the original recording stays on disk')
  expect(button(node, 'Remove reviewed copies').disabled).toBe(true)
  await confirmReview(node); expect(button(node, 'Remove reviewed copies').disabled).toBe(false)
  button(node, 'Remove reviewed copies').click(); await settle()
  expect(api.confirmMusicCleanupPlan).toHaveBeenCalledExactlyOnceWith(plan.id, { plan_sha256: plan.plan_sha256, confirm_cleanup: true }, expect.any(AbortSignal))
  expect(node.textContent).toContain('Reviewed cleanup completed.')
})
it('keeps an uncertain request pending and reconnects without automatically deleting again', async () => {
  vi.mocked(api.confirmMusicCleanupPlan).mockRejectedValueOnce(new Error('/private/token'))
  vi.mocked(api.getMusicCleanupPlan).mockResolvedValue({ ...plan, status: 'completed' })
  const node = await mount(); await open(node); await prepare(node); await confirmReview(node)
  button(node, 'Remove reviewed copies').click(); await settle()
  expect(node.textContent).toContain('Reload saved progress before trying again')
  expect(node.textContent).not.toContain('/private/token'); expect(button(node, 'Remove reviewed copies').disabled).toBe(true)
  button(node, 'Reload saved cleanup progress').click(); await settle()
  expect(node.textContent).toContain('Reviewed cleanup completed.'); expect(api.confirmMusicCleanupPlan).toHaveBeenCalledTimes(1)
})
it('opens a saved partial plan from inventory and requires fresh review to resume', async () => {
  vi.mocked(api.getMusicArtifacts).mockResolvedValue({ ...inventory, groups: [{ id: 'stage:one', kind: 'cleanup_staging', root_alias: 'cleanup_staging', disposition: 'blocked', dependencies: [{ kind: 'cleanup_plan', id: plan.id }] }] })
  vi.mocked(api.getMusicCleanupPlan).mockResolvedValue({ ...plan, status: 'partial' })
  vi.mocked(api.getMusicCleanupReceipt).mockResolvedValue({ ...receipt, status: 'partial', items: [{ job_id: id, status: 'pending' }] })
  const node = await mount(); await open(node); button(node, 'Open saved cleanup plan bbbbbbbb').click(); await settle()
  expect(button(node, 'Resume reviewed cleanup').disabled).toBe(true)
  await confirmReview(node); expect(button(node, 'Resume reviewed cleanup').disabled).toBe(false)
  expect(api.confirmMusicCleanupPlan).not.toHaveBeenCalled()
})
it('ignores and aborts late reads when closed or unmounted without cancelling server cleanup', async () => {
  let finish: ((value: MusicArtifactInventory) => void) | undefined
  vi.mocked(api.getMusicArtifacts).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const node = await mount(); await open(node)
  const signal = vi.mocked(api.getMusicArtifacts).mock.calls[0]?.[0]
  button(node, 'Music storage review').click(); await settle(); expect(signal?.aborted).toBe(true)
  finish?.(inventory); await settle(); expect(node.querySelector('ul')).toBeNull()
  expect(api.confirmMusicCleanupPlan).not.toHaveBeenCalled()
})
