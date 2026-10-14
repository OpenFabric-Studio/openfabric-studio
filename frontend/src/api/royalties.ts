import { apiFetch } from './http'
import { parseAgreementsResponse, parseAgreementCreate, parseAgreement, parseReleasesResponse,
  parseReleaseCreate, parseRelease, parseCsvImportRequest, parseImportPreview, parseCsvImportCommit,
  parseImportResult, parseAdjustmentCreate, parseAdjustment, parsePaymentCreate, parsePayment,
  parseRoyaltyStatement } from './contracts'
import type { AgreementCreate, ReleaseCreate, CsvImportRequest, CsvImportCommit, AdjustmentCreate, PaymentCreate } from './contracts'

const base = '/api/artist-accounting'
function json(body: unknown, signal?: AbortSignal): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }
}
export async function listAgreements(signal?: AbortSignal) {
  return (await apiFetch(`${base}/agreements`, { signal }, parseAgreementsResponse)).agreements
}
export async function createAgreement(body: AgreementCreate, signal?: AbortSignal) {
  return apiFetch(`${base}/agreements`, json(parseAgreementCreate(body), signal), parseAgreement)
}
export async function listReleases(signal?: AbortSignal) {
  return (await apiFetch(`${base}/releases`, { signal }, parseReleasesResponse)).releases
}
export async function createRelease(body: ReleaseCreate, signal?: AbortSignal) {
  return apiFetch(`${base}/releases`, json(parseReleaseCreate(body), signal), parseRelease)
}
export async function previewImport(body: CsvImportRequest, signal?: AbortSignal) {
  return apiFetch(`${base}/imports/preview`, json(parseCsvImportRequest(body), signal), parseImportPreview)
}
export async function commitImport(body: CsvImportCommit, signal?: AbortSignal) {
  return apiFetch(`${base}/imports/commit`, json(parseCsvImportCommit(body), signal), parseImportResult)
}
export async function createAdjustment(body: AdjustmentCreate, signal?: AbortSignal) {
  return apiFetch(`${base}/adjustments`, json(parseAdjustmentCreate(body), signal), parseAdjustment)
}
export async function recordPayment(body: PaymentCreate, signal?: AbortSignal) {
  return apiFetch(`${base}/payments`, json(parsePaymentCreate(body), signal), parsePayment)
}
export async function getStatement(agreementId: string, signal?: AbortSignal) {
  if (!/^[0-9a-f]{32}$/.test(agreementId)) throw new Error('Invalid royalty agreement identifier')
  return apiFetch(`${base}/agreements/${agreementId}/statement`, { signal }, parseRoyaltyStatement)
}
