// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import * as api from '../../api/regionalDatasets'
import { ApiError } from '../../api/http'
import RegionalAcousticScan from './RegionalAcousticScan.vue'
import { acousticJobFixture, acousticReviewFixture } from './regionalAcousticTestFixtures'

vi.mock('../../api/regionalDatasets', async original => ({ ...await original<typeof import('../../api/regionalDatasets')>(),
  listRegionalAcousticScans: vi.fn(), startRegionalAcousticScan: vi.fn(), getRegionalAcousticScan: vi.fn(), cancelRegionalAcousticScan: vi.fn() }))
let app: App | undefined
async function settle() { for (let n = 0; n < 20; n++) await nextTick() }
async function mount(review = ref(acousticReviewFixture())) {
  const apply = vi.fn(), node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(RegionalAcousticScan, { review: review.value, onApply: apply }) }).use(i18n)
  app.mount(node); await settle(); return { node, apply }
}
function button(node: HTMLElement, text: string) {
  const result = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!result) throw new Error(`Missing ${text}`)
  return result
}
beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); vi.useFakeTimers(); vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: []}) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
it('keeps suspected shared audio together unless a reviewer explicitly releases an auditioned pair', async () => {
  const job = acousticJobFixture()
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [job]})
  const { node, apply } = await mount()
  expect(api.startRegionalAcousticScan).not.toHaveBeenCalled()
  expect(node.textContent).toContain('Possible shared audio')
  expect(node.textContent).toContain('not proof that recordings are independent')
  const distinct = node.querySelector<HTMLInputElement>('input[type="checkbox"]')
  expect(distinct?.disabled).toBe(true)
  button(node, 'Use this scan for the dataset split').click()
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ scan_id: job.id, scan_sha256: job.result?.sha256, decisions: [] }))
})
it.each([new Error('connection lost'), new ApiError('Invalid response from server', 200), new ApiError('Request timeout', 408)])('retains an uncertain submission across reload and replays its exact reviewed request only on Resume (%s)', async failure => {
  vi.mocked(api.startRegionalAcousticScan).mockRejectedValueOnce(failure)
  const { node } = await mount()
  button(node, 'Scan selected excerpts').click(); await settle()
  const sent = vi.mocked(api.startRegionalAcousticScan).mock.calls[0]?.[0]
  expect(sent).toBeDefined()
  app?.unmount(); app = undefined
  const reopened = await mount()
  expect(api.startRegionalAcousticScan).toHaveBeenCalledTimes(1)
  vi.mocked(api.startRegionalAcousticScan).mockImplementation(async request => ({ ...acousticJobFixture(), request }))
  button(reopened.node, 'Resume the saved scan submission').click(); await settle()
  expect(vi.mocked(api.startRegionalAcousticScan).mock.calls[1]?.[0]).toEqual(sent)
})
it('shows changed selections and blocks applying a stale source report', async () => {
  const job = acousticJobFixture()
  const excerpt = job.result?.report.excerpts[0]
  if (!excerpt) throw new Error('Missing fixture')
  excerpt.source_sha256 = '9'.repeat(64)
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [job]})
  const { node, apply } = await mount()
  expect(node.textContent).toContain('different selected audio or excerpts')
  expect(button(node, 'Use this scan for the dataset split').disabled).toBe(true)
  button(node, 'Use this scan for the dataset split').click(); expect(apply).not.toHaveBeenCalled()
})

it('requires paired auditions and a complete confirmed review before releasing a candidate edge', async () => {
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [acousticJobFixture('d'.repeat(32), 0, true)]})
  const { node, apply } = await mount()
  node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await settle()
  expect(button(node, 'Use this scan for the dataset split').disabled).toBe(true)
  const reviewer = node.querySelector<HTMLInputElement>('input[maxlength="120"]'), note = node.querySelector<HTMLTextAreaElement>('textarea')
  if (!reviewer || !note) throw new Error('Missing reviewer controls')
  reviewer.value = 'Fixture listener'; reviewer.dispatchEvent(new Event('input'))
  note.value = 'Different phrasing in both retained excerpts and source context.'; note.dispatchEvent(new Event('input'))
  node.querySelector<HTMLFieldSetElement>('fieldset')?.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await settle()
  button(node, 'Use this scan for the dataset split').click()
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({decisions: [expect.objectContaining({decision: 'distinct_after_review', reviewed_by: 'Fixture listener', note: note.value})]}))
})
it('withdraws a distinct draft when a retained audition cannot be opened', async () => {
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [acousticJobFixture('d'.repeat(32), 0, true)]})
  const { node } = await mount(), distinct = node.querySelector<HTMLInputElement>('input[type="checkbox"]')
  distinct?.click(); await settle()
  node.querySelector('audio')?.dispatchEvent(new Event('error')); await settle()
  expect(distinct?.checked).toBe(false); expect(distinct?.disabled).toBe(true)
  expect(node.textContent).toContain('A saved audition could not be opened')
})

it('preserves draft decisions across a remote review revision and requires explicit re-review', async () => {
  const job = acousticJobFixture('d'.repeat(32), 0, true), review = ref(acousticReviewFixture())
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [job]})
  const { node, apply } = await mount(review)
  node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await settle()
  const reviewer = node.querySelector<HTMLInputElement>('input[maxlength="120"]'), note = node.querySelector<HTMLTextAreaElement>('textarea')
  if (!reviewer || !note) throw new Error('Missing review controls')
  reviewer.value = 'Local reviewer'; reviewer.dispatchEvent(new Event('input')); note.value = 'My unsaved review'; note.dispatchEvent(new Event('input'))
  node.querySelector('fieldset input[type="checkbox"]')?.dispatchEvent(new MouseEvent('click', {bubbles: true})); await settle()
  review.value = {...review.value, revision: 2, sha256: '8'.repeat(64)}; await settle()
  expect(note.value).toBe('My unsaved review')
  expect(button(node, 'Use this scan for the dataset split').disabled).toBe(true)
  expect(node.textContent).toContain('The saved dataset review changed')
  button(node, 'Review my decisions against this revision').click(); await settle()
  button(node, 'Use this scan for the dataset split').click()
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({decisions: [expect.objectContaining({reviewed_by: 'Local reviewer', note: 'My unsaved review'})]}))
})

async function draftDecision(node: HTMLElement, noteText = 'My unsaved decision') {
  node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await settle()
  const reviewer = node.querySelector<HTMLInputElement>('input[maxlength="120"]'), note = node.querySelector<HTMLTextAreaElement>('textarea')
  if (!reviewer || !note) throw new Error('Missing decision controls')
  reviewer.value = 'Local reviewer'; reviewer.dispatchEvent(new Event('input'))
  note.value = noteText; note.dispatchEvent(new Event('input'))
  node.querySelector('fieldset input[type="checkbox"]')?.dispatchEvent(new MouseEvent('click', {bubbles: true})); await settle()
  return note
}
it('keeps the selected scan and unsaved decisions when history refresh discovers a newer scan', async () => {
  const job = acousticJobFixture('d'.repeat(32), 0, true)
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValueOnce({jobs: [job]})
  const { node } = await mount(), note = await draftDecision(node)
  const newer = {...job, id: '1'.repeat(32)}
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [newer, job]})
  button(node, 'Refresh saved work').click(); await settle()
  expect(node.querySelector('select')?.value).toBe(job.id)
  expect(note.isConnected).toBe(true); expect(note.value).toBe('My unsaved decision')
  expect(button(node, 'Scan selected excerpts').disabled).toBe(true)
})
it('accepts its own saved decisions without a revision conflict', async () => {
  const job = acousticJobFixture('d'.repeat(32), 0, true), review = ref(acousticReviewFixture())
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [job]})
  const { node, apply } = await mount(review); await draftDecision(node)
  button(node, 'Use this scan for the dataset split').click()
  const saved: unknown = apply.mock.calls[0]?.[0]
  const { parseRegionalAcousticReview } = await import('../../api/generated')
  review.value = {...review.value, revision: 2, sha256: '8'.repeat(64), request: {...review.value.request, acoustic_review: parseRegionalAcousticReview(saved)}}
  await settle()
  expect(node.textContent).not.toContain('The saved dataset review changed')
  expect(node.textContent).not.toContain('Discard draft decisions')
  expect(node.querySelector('textarea')?.value).toBe('My unsaved decision')
})
it('uses a remote saved decision only after explicitly discarding local edits', async () => {
  const job = acousticJobFixture('d'.repeat(32), 0, true), review = ref(acousticReviewFixture())
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [job]})
  const { node } = await mount(review); await draftDecision(node)
  review.value = {...review.value, revision: 2, sha256: '8'.repeat(64), request: {...review.value.request, acoustic_review: {
    scan_id: job.id, scan_sha256: 'd'.repeat(64), decisions: [{candidate_id: 'c'.repeat(64), decision: 'distinct_after_review', reviewed_by: 'Remote reviewer', reviewed_at: '2026-10-17', note: 'Their saved decision'}]}}}
  await settle()
  expect(node.querySelector('textarea')?.value).toBe('My unsaved decision')
  button(node, 'Discard draft decisions and use the saved review').click(); await settle()
  expect(node.querySelector('textarea')?.value).toBe('Their saved decision')
  expect(node.textContent).not.toContain('The saved dataset review changed')
})

it('preserves an existing identified keep-together decision when reattaching the scan', async () => {
  const job = acousticJobFixture(), review = ref(acousticReviewFixture())
  const decision = {candidate_id: 'c'.repeat(64), decision: 'keep_together' as const, reviewed_by: 'Earlier reviewer', reviewed_at: '2026-10-17', note: 'Same phrase from the original session.'}
  review.value.request.acoustic_review = {scan_id: job.id, scan_sha256: 'd'.repeat(64), decisions: [decision]}
  vi.mocked(api.listRegionalAcousticScans).mockResolvedValue({jobs: [job]})
  const {node, apply} = await mount(review)
  button(node, 'Use this scan for the dataset split').click()
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({decisions: [decision]}))
})
