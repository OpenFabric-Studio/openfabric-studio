// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import RoyaltyStatementsPanel from './RoyaltyStatementsPanel.vue'
import { royaltiesEn } from '../../locales/royalties'
import type { Agreement, ImportPreview, Release, RoyaltyStatement } from '../../api/contracts'

const artistId = 'a'.repeat(32), agreementId = 'b'.repeat(32), releaseId = 'c'.repeat(32)
const agreement: Agreement = { id: agreementId, artist_id: artistId, percentage: '12.5', basis: 'gross', currency: 'EUR',
  permitted_deduction_categories: [], eligibility_start: '2026-01-01', eligibility_end: null,
  evidence_ref: '/private/contract.pdf', evidence_version: 'signed-v1', created_at: 'now', authenticated_actor: null }
const release: Release = { id: releaseId, agreement_id: agreementId, run_id: 'd'.repeat(32), audio_version_id: 'e'.repeat(32),
  platform: 'Synthetic platform', master_id: 'synthetic-master', master_sha256: 'f'.repeat(64), isrc: null, upc: null,
  release_date: '2026-01-01', eligible: true, created_at: 'now', authenticated_actor: null }
const statement: RoyaltyStatement = { schema_version: 1, agreement_id: agreementId, artist_id: artistId,
  agreement_evidence_version: 'signed-v1', percentage: '12.5', basis: 'gross', currency: 'EUR',
  eligibility_start: '2026-01-01', eligibility_end: null, releases: [release], import_ids: [], entries: [], adjustments: [], payments: [],
  royalty_accrued: '10', adjustments_total: '0', payments_total: '0', balance_due: '10', authenticated_actor: null,
  actor_context: 'unauthenticated_local_operator' }
const preview: ImportPreview = { content_sha256: '1'.repeat(64), preview_hash: '2'.repeat(64), duplicate_content: false,
  existing_import_id: null, rows: [], errors: [], totals: [{ agreement_id: agreementId, currency: 'EUR', row_count: 1,
    revenue: '80', deductions: '0', input_total: '80', royalty_base: '80', royalty_due: '10' }], input_row_count: 1, valid_row_count: 1 }
let app: App | undefined
let agreements: Agreement[]
let currentPreview: ImportPreview
let fetchMock: ReturnType<typeof vi.fn>
async function fixtureFetch(url: string, init?: RequestInit): Promise<Response> {
    if (url.endsWith('/agreements') && init?.method !== 'POST') return new Response(JSON.stringify({ agreements }))
    if (url.endsWith('/agreements')) return new Response(JSON.stringify(agreement))
    if (url.endsWith('/releases')) return new Response(JSON.stringify({ releases: [release] }))
    if (url.endsWith('/preview')) return new Response(JSON.stringify(currentPreview))
    if (url.endsWith('/commit')) return new Response(JSON.stringify({ id: '3'.repeat(32), distributor: 'synthetic',
      content_sha256: preview.content_sha256, preview_hash: preview.preview_hash, row_count: 1, totals: preview.totals, created_at: 'now' }))
    if (url.endsWith('/statement')) return new Response(JSON.stringify(statement))
    if (url.endsWith('/payments')) return new Response(JSON.stringify({ id: '6'.repeat(32), agreement_id: agreementId,
      amount: '5', currency: 'EUR', occurred_on: '2026-10-14', evidence_ref: 'private-receipt', idempotency_key: 'paid-1', created_at: 'now' }))
    throw new Error(`Unexpected synthetic route ${url}`)
}
beforeEach(() => {
  agreements = [agreement]; currentPreview = preview
  fetchMock = vi.fn(fixtureFetch)
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function settle() { for (let index = 0; index < 12; index++) { await Promise.resolve(); await nextTick() } }
async function mount(selectedArtist = ref(artistId), active = ref(true)) {
  app = createApp({ render: () => h(RoyaltyStatementsPanel, { artistId: selectedArtist.value, active: active.value }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { royalties: royaltiesEn } } }))
  const container = document.body.appendChild(document.createElement('div'))
  app.mount(container); await settle(); return container
}
function button(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}
async function input(container: HTMLElement, label: string, value: string) {
  const field = container.querySelector(`[aria-label="${label}"]`)
  if (!(field instanceof HTMLInputElement) && !(field instanceof HTMLTextAreaElement)) throw new Error(`Missing field: ${label}`)
  field.value = value; field.dispatchEvent(new Event('input', { bubbles: true })); await settle()
}
async function csv(container: HTMLElement, file = new File([`release,date,amount,currency\n${releaseId},2026-10,80,EUR\n`], 'synthetic.csv')) {
  const field = container.querySelector('input[type=file]')
  if (!(field instanceof HTMLInputElement)) throw new Error('Missing report file input')
  const transfer = new DataTransfer(); transfer.items.add(file); field.files = transfer.files
  field.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
async function configureImport(container: HTMLElement) {
  await input(container, royaltiesEn.distributor, 'synthetic')
  await input(container, royaltiesEn.mapping.release_id, 'release')
  await input(container, royaltiesEn.mapping.period, 'date')
  await input(container, royaltiesEn.mapping.amount, 'amount')
  await input(container, royaltiesEn.mapping.currency, 'currency')
  await csv(container)
}

it('starts without a rate or rights assumption and describes local declaration limits', async () => {
  const container = await mount()
  const percentage = container.querySelector(`[aria-label="${royaltiesEn.percentage}"]`)
  if (!(percentage instanceof HTMLInputElement)) throw new Error('Missing percentage')
  expect(percentage.value).toBe('')
  expect(button(container, royaltiesEn.createAgreement).disabled).toBe(true)
  expect(container.textContent).toContain(royaltiesEn.actorHint)
  expect(container.textContent).toContain(royaltiesEn.releaseGate)
  expect(container.textContent).not.toContain('/private/contract')
})

it('invalidates a reviewed import when its mapping changes and commits the exact hash', async () => {
  const container = await mount()
  await configureImport(container)
  button(container, royaltiesEn.preview).click(); await settle()
  expect(button(container, royaltiesEn.commit).disabled).toBe(false)
  await input(container, royaltiesEn.mapping.amount, 'revenue')
  expect(button(container, royaltiesEn.commit).disabled).toBe(true)
  button(container, royaltiesEn.preview).click(); await settle()
  button(container, royaltiesEn.commit).click(); await settle()
  const commitCall = fetchMock.mock.calls.find(call => String(call[0]).endsWith('/commit'))
  const init: unknown = commitCall?.[1]
  if (!init || typeof init !== 'object' || !('body' in init) || typeof init.body !== 'string') throw new Error('Missing commit')
  const body: unknown = JSON.parse(init.body)
  expect(body).toMatchObject({ preview_hash: preview.preview_hash, request: { mapping: { amount: 'revenue' } } })
})

it('shows rejected rows and blocks import without exposing raw error details', async () => {
  currentPreview = { ...preview, errors: [{ row_number: 2, code: 'currency_mismatch' }], valid_row_count: 0 }
  const container = await mount(); await configureImport(container)
  button(container, royaltiesEn.preview).click(); await settle()
  expect(container.textContent).toContain(royaltiesEn.errors.currency_mismatch)
  expect(button(container, royaltiesEn.commit).disabled).toBe(true)
  fetchMock.mockRejectedValueOnce(new Error('/private/report.csv'))
  button(container, royaltiesEn.preview).click(); await settle()
  expect(container.querySelector('[role=alert]')?.textContent).toContain(royaltiesEn.errors.unknown)
  expect(container.textContent).not.toContain('/private/report')
})

it('rejects reports larger than one MB before reading or submitting them', async () => {
  const container = await mount()
  await csv(container, new File(['x'.repeat(1_000_001)], 'synthetic.csv'))
  expect(container.textContent).toContain(royaltiesEn.errors.report_too_large)
  expect(button(container, royaltiesEn.preview).disabled).toBe(true)
  expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith('/preview'))).toBe(false)
})

it('ignores responses after artist selection changes or the panel becomes inactive', async () => {
  let resolve: (response: Response) => void = () => { throw new Error('Not initialized') }
  fetchMock.mockImplementationOnce(() => new Promise<Response>(accept => { resolve = accept }))
  const selectedArtist = ref(artistId), active = ref(true)
  const container = await mount(selectedArtist, active)
  agreements = [{ ...agreement, id: '4'.repeat(32), artist_id: '5'.repeat(32), evidence_version: 'second-v1' }]
  selectedArtist.value = '5'.repeat(32); await settle()
  resolve(new Response(JSON.stringify({ agreements: [agreement] }))); await settle()
  expect(container.textContent).toContain('second-v1')
  expect(container.textContent).not.toContain('signed-v1')
  active.value = false; await settle()
  expect(container.textContent).toBe('')
})

it('exports only a schema-validated shareable statement as local JSON', async () => {
  let exported: Blob | undefined
  vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => {
    if (!(blob instanceof Blob)) throw new Error('Expected JSON blob')
    exported = blob; return 'blob:synthetic'
  })
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const container = await mount()
  button(container, royaltiesEn.download).click(); await settle()
  if (!exported) throw new Error('Missing statement export')
  const text = await exported.text(), parsed: unknown = JSON.parse(text)
  expect(parsed).toEqual(statement)
  expect(text).not.toContain('/private')
  expect(revoke).toHaveBeenCalledWith('blob:synthetic')
})

it('requires an actual-payment declaration and records decimal strings', async () => {
  const container = await mount()
  await input(container, royaltiesEn.paymentAmount, '5')
  await input(container, royaltiesEn.paymentDate, '2026-10-14')
  await input(container, royaltiesEn.paymentEvidence, 'private-receipt')
  await input(container, royaltiesEn.paymentKey, 'paid-1')
  expect(button(container, royaltiesEn.recordPayment).disabled).toBe(true)
  const label = [...container.querySelectorAll('label')].find(item => item.textContent?.includes(royaltiesEn.paymentConfirmed))
  const confirm = label?.querySelector('input')
  if (!(confirm instanceof HTMLInputElement)) throw new Error('Missing payment declaration')
  confirm.checked = true; confirm.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(button(container, royaltiesEn.recordPayment).disabled).toBe(false)
  button(container, royaltiesEn.recordPayment).click(); await settle()
  const init: unknown = fetchMock.mock.calls.find(call => String(call[0]).endsWith('/payments'))?.[1]
  if (!init || typeof init !== 'object' || !('body' in init) || typeof init.body !== 'string') throw new Error('Missing payment record')
  const body: unknown = JSON.parse(init.body)
  expect(body).toMatchObject({ agreement_id: agreementId, amount: '5', currency: 'EUR', idempotency_key: 'paid-1' })
  expect(container.textContent).toContain(royaltiesEn.paymentHint)
})

it('records only explicitly entered agreement terms with no default rate or deductions', async () => {
  const container = await mount()
  await input(container, royaltiesEn.percentage, '17.25')
  await input(container, royaltiesEn.currency, 'EUR')
  await input(container, royaltiesEn.start, '2026-01-01')
  await input(container, royaltiesEn.end, '2026-12-31')
  await input(container, royaltiesEn.evidence, 'private-contract-2')
  await input(container, royaltiesEn.version, 'signed-v2')
  const basis = container.querySelector(`[aria-label="${royaltiesEn.basis}"]`)
  if (!(basis instanceof HTMLSelectElement)) throw new Error('Missing agreement basis')
  basis.value = 'gross'; basis.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(button(container, royaltiesEn.createAgreement).disabled).toBe(true)
  const label = [...container.querySelectorAll('label')].find(item => item.textContent?.includes(royaltiesEn.noDeductions))
  const confirmed = label?.querySelector('input')
  if (!(confirmed instanceof HTMLInputElement)) throw new Error('Missing deduction declaration')
  confirmed.checked = true; confirmed.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(button(container, royaltiesEn.createAgreement).disabled).toBe(false)
  button(container, royaltiesEn.createAgreement).click(); await settle()
  const init: unknown = fetchMock.mock.calls.find(call => String(call[0]).endsWith('/agreements') && call[1]?.method === 'POST')?.[1]
  if (!init || typeof init !== 'object' || !('body' in init) || typeof init.body !== 'string') throw new Error('Missing agreement record')
  const body: unknown = JSON.parse(init.body)
  expect(body).toMatchObject({ artist_id: artistId, percentage: '17.25', basis: 'gross', currency: 'EUR',
    permitted_deduction_categories: [], eligibility_start: '2026-01-01', eligibility_end: '2026-12-31', evidence_version: 'signed-v2' })
})

it('does not start another statement request when an import completes after teardown', async () => {
  let resolve: (response: Response) => void = () => { throw new Error('Not initialized') }
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/commit')) return new Promise<Response>(accept => { resolve = accept })
    return fixtureFetch(url, init)
  })
  const container = await mount(); await configureImport(container)
  button(container, royaltiesEn.preview).click(); await settle()
  button(container, royaltiesEn.commit).click(); await settle()
  const statementCalls = fetchMock.mock.calls.filter(call => String(call[0]).endsWith('/statement')).length
  app?.unmount(); app = undefined
  resolve(new Response(JSON.stringify({ id: '3'.repeat(32), distributor: 'synthetic', content_sha256: preview.content_sha256,
    preview_hash: preview.preview_hash, row_count: 1, totals: preview.totals, created_at: 'now' })))
  await settle()
  expect(fetchMock.mock.calls.filter(call => String(call[0]).endsWith('/statement'))).toHaveLength(statementCalls)
})
