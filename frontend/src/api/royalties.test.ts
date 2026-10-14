// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import * as api from './royalties'

const agreementId = 'a'.repeat(32), artistId = 'b'.repeat(32)
const agreement = { id: agreementId, artist_id: artistId, percentage: '12.5', basis: 'gross', currency: 'EUR',
  permitted_deduction_categories: [], eligibility_start: '2026-01-01', eligibility_end: null,
  evidence_ref: 'private-contract', evidence_version: 'signed-v1', created_at: 'now', authenticated_actor: null }
afterEach(() => vi.unstubAllGlobals())

it('validates local agreement responses and forwards cancellation', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ agreements: [agreement] })))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.listAgreements(controller.signal)).toEqual([agreement])
  expect(fetch).toHaveBeenCalledWith('/api/artist-accounting/agreements', { signal: controller.signal })
})

it('rejects floating money and unsafe statement identities before returning data', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ agreements: [{ ...agreement, percentage: 12.5 }] })))
  vi.stubGlobal('fetch', fetch)
  await expect(api.listAgreements()).rejects.toThrow('Invalid AgreementsResponse')
  fetch.mockClear()
  await expect(api.getStatement('../private')).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('binds commits to the complete request and exact preview hash', async () => {
  const request = { distributor: 'synthetic', csv_text: 'release,date,amount,currency\n',
    mapping: { release_id: 'release', period: 'date', amount: 'amount', currency: 'currency' } }
  const command = { request, preview_hash: 'c'.repeat(64) }
  const result = { id: 'd'.repeat(32), distributor: 'synthetic', content_sha256: 'e'.repeat(64), preview_hash: command.preview_hash,
    row_count: 1, totals: [], created_at: 'now', authenticated_actor: null }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(result)))
  vi.stubGlobal('fetch', fetch)
  expect(await api.commitImport(command)).toEqual(result)
  const init: unknown = fetch.mock.calls[0]?.[1]
  if (!init || typeof init !== 'object' || !('body' in init) || typeof init.body !== 'string') throw new Error('Missing commit body')
  const body: unknown = JSON.parse(init.body)
  expect(body).toEqual(command)
})
