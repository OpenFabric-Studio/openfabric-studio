// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import ArtistProductionPanel from './ArtistProductionPanel.vue'
vi.mock('./SingerRangePanel.vue', () => ({ default: { template: '<section data-singer-range-fixture />' } }))
vi.mock('./RoyaltyStatementsPanel.vue', () => ({ default: { template: '<section data-royalty-statements-fixture />' } }))
vi.mock('./RecordingQualityPanel.vue', () => ({ default: { template: '<section data-recording-qc-fixture />' } }))
import { artistProductionEn } from '../../locales/artistProduction'
import en from '../../locales/en'
import { voiceProfile } from './voiceTestFixtures'
import type { ArtistConsentVersion, ArtistProfile, SourceAsset, VoiceProfileResponse } from '../../api/contracts'
import { parseAmendSourceRequest, parseArtistConsentRequest } from '../../api/generated'

const voice = voiceProfile('a'.repeat(32))
const artist: ArtistProfile = { id: 'b'.repeat(32), name: 'Fixture artist', voice_id: voice.id, created_at: 'now', current_consent_version_id: 'c'.repeat(32) }
const consent: ArtistConsentVersion = { id: 'c'.repeat(32), artist_id: artist.id, revision: 1, created_at: 'now', status: 'active', signed_evidence_ref: 'signed-agreement', operator_label: 'Operator', human_declaration: 'I reviewed the signed artist agreement.', allowed_operations: ['train', 'convert', 'export'], allowed_uses: ['production'], territories: [], actor_authenticated: false, authenticated_actor_id: null }
function asset(id: string, role: 'guide_vocal' | 'accompaniment', origin: SourceAsset['origin'] = 'human_recorded'): SourceAsset {
  const provider = origin === 'ai_generated' ? 'Suno' : 'Artist'
  return { schema_version: 1, id, sha256: 'd'.repeat(64), role, origin, provider, created_at: 'now', classification_basis: 'user_declaration', reference_only: origin === 'unknown' || origin === 'ai_generated', parent_ids: [], transformation: '', declaration: { role, origin, provider, rights_holder: 'Artist', original_filename: `${role}.wav`, evidence_ref: 'source-agreement', permission_scope: 'Production fixture grant', acquired_at: '2026-10-01', source_artifact: 'original-source-id', permitted_uses: ['production'] }, reviews: [{ id: 'e'.repeat(32), status: 'approved', declaration_revision_id: id, reviewer_label: 'Operator', evidence_ref: 'source-agreement', created_at: 'now', authenticated_actor: null }] }
}
function amended(source: SourceAsset, init: RequestInit): SourceAsset {
  const request = parseAmendSourceRequest(JSON.parse(String(init.body)))
  if (!source.declaration) throw new Error('Missing source declaration fixture')
  return { ...source, declaration: request.declaration, declaration_revisions: [
    { id: '6'.repeat(32), created_at: 'original', declaration: source.declaration, reviewer_label: 'Original operator' },
    { ...request, id: '7'.repeat(32), created_at: 'amended' },
  ], reviews: [...source.reviews ?? [], { id: '8'.repeat(32), created_at: 'amended', status: 'unknown', reviewer_label: request.reviewer_label, evidence_ref: request.declaration.evidence_ref || 'evidence_missing' }] }
}
let app: App | undefined
let profiles: ArtistProfile[]
let consents: ArtistConsentVersion[]
let sources: SourceAsset[]
let fetchMock: ReturnType<typeof vi.fn<(url: string, init?: RequestInit) => Promise<Response>>>
beforeEach(() => {
  profiles = [artist]; consents = []; sources = []
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/artists') return new Response(JSON.stringify({ artists: profiles }))
    if (url.endsWith('/consents') && init?.method === 'POST') return new Response(JSON.stringify({ ...consent, id: 'f'.repeat(32), revision: 2, status: 'revoked' }))
    if (url.endsWith('/consents')) return new Response(JSON.stringify({ artist_id: artist.id, versions: consents }))
    if (url.endsWith('/assets')) return new Response(JSON.stringify({ assets: sources }))
    if (url.endsWith('/runs')) return new Response(JSON.stringify({ runs: [] }))
    throw new Error(`Unexpected local fixture route ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })
async function settle() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick() } }
async function mount(selected = ref<VoiceProfileResponse>(voice)) {
  app = createApp({ render: () => h(ArtistProductionPanel, { voice: selected.value }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { ...en, artistProduction: artistProductionEn } } }))
  const container = document.body.appendChild(document.createElement('div'))
  app.mount(container); await settle(); return container
}
function button(container: HTMLElement, text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!found) throw new Error(`Missing button: ${text}`)
  return found
}
async function input(container: HTMLElement, label: string, value: string) {
  const field = container.querySelector(`[aria-label="${label}"]`)
  if (!(field instanceof HTMLInputElement) && !(field instanceof HTMLTextAreaElement)) throw new Error(`Missing field: ${label}`)
  field.value = value; field.dispatchEvent(new Event('input', { bubbles: true })); await settle()
}
function labeledControl(container: HTMLElement, text: string): HTMLInputElement | HTMLSelectElement {
  const wrapper = [...container.querySelectorAll('label')].find(label => label.querySelector('span')?.textContent === text || label.textContent?.trim() === text)
  const control = wrapper?.querySelector('input, select')
  if (!(control instanceof HTMLInputElement) && !(control instanceof HTMLSelectElement)) throw new Error(`Missing labeled control: ${text}`)
  return control
}
async function choose(container: HTMLElement, text: string, value: string) {
  const control = labeledControl(container, text)
  control.value = value; control.dispatchEvent(new Event('change', { bubbles: true })); control.dispatchEvent(new Event('input', { bubbles: true })); await settle()
}
it('shows missing evidence as a blocker even when the checkpoint is usable', async () => {
  const container = await mount(ref({ ...voice, usable: true }))
  expect(container.textContent).toContain(artistProductionEn.legacy)
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
  expect(container.textContent).toContain('45')
  expect(container.textContent).toContain(artistProductionEn.actorHint)
})
it('keeps unknown sources out of clean production after an approved human review', async () => {
  consents = [consent]
  sources = [asset('1'.repeat(32), 'guide_vocal', 'unknown'), asset('2'.repeat(32), 'accompaniment')]
  const container = await mount()
  expect(container.textContent).toContain(artistProductionEn.referenceOnly)
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
  button(container, artistProductionEn.render).click(); await settle()
  expect(fetchMock.mock.calls.some(call => call[1]?.method === 'POST' && call[0].endsWith('/runs'))).toBe(false)
})
it('records revocation and disables production without deleting evidence', async () => {
  consents = [consent]
  const container = await mount()
  await input(container, artistProductionEn.operator, 'Local operator')
  await input(container, artistProductionEn.declaration, 'The artist revoked permission for further use.')
  button(container, artistProductionEn.revoke).click(); await settle()
  expect(container.textContent).toContain(artistProductionEn.status.revoked)
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
  const request = fetchMock.mock.calls.find(call => call[1]?.method === 'POST')?.[1]
  expect(JSON.parse(String(request?.body))).toMatchObject({ status: 'revoked', operator_label: 'Local operator' })
})
it('ignores a profile response after the selected voice changes', async () => {
  let release: (response: Response) => void = () => { throw new Error('Not initialized') }
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
  const selected = ref<VoiceProfileResponse>(voice)
  const container = await mount(selected)
  profiles = [{ ...artist, id: '9'.repeat(32), voice_id: '8'.repeat(32), name: 'Second artist' }]
  selected.value = { ...voice, id: '8'.repeat(32) }; await settle()
  release(new Response(JSON.stringify({ artists: [artist] }))); await settle()
  expect(container.textContent).toContain('Second artist')
  expect(container.textContent).not.toContain('Fixture artist')
})
it('sanitizes a failed evidence load and keeps actions blocked', async () => {
  fetchMock.mockRejectedValueOnce(new Error('/private/library/secret.wav'))
  const container = await mount()
  expect(container.querySelector('[role=alert]')?.textContent).toContain(artistProductionEn.errors.unknown)
  expect(container.textContent).not.toContain('/private/')
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
})
it('appends source evidence without rewriting a generated source identity', async () => {
  const generated = asset('1'.repeat(32), 'guide_vocal', 'ai_generated')
  sources = [generated]
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/declarations') && init) return new Response(JSON.stringify(amended(generated, init)))
    if (url === '/api/artists') return new Response(JSON.stringify({ artists: profiles }))
    if (url.endsWith('/consents')) return new Response(JSON.stringify({ artist_id: artist.id, versions: consents }))
    if (url.endsWith('/assets')) return new Response(JSON.stringify({ assets: sources }))
    if (url.endsWith('/runs')) return new Response(JSON.stringify({ runs: [] }))
    throw new Error('Unexpected fixture route')
  })
  const container = await mount()
  button(container, 'Revise source evidence').click(); await settle()
  await input(container, 'Revision reviewer label', 'Local operator')
  await input(container, 'Revised evidence reference', 'new-agreement')
  await input(container, 'Revised permission scope', 'Evaluation reference only under new terms.')
  expect(container.querySelector('[data-source-revision] select')).toBeNull()
  button(container, 'Append evidence revision').click(); await settle()
  const call = fetchMock.mock.calls.find(call => call[0].endsWith('/declarations'))
  const request = parseAmendSourceRequest(JSON.parse(String(call?.[1]?.body)))
  expect(request).toMatchObject({ reviewer_label: 'Local operator', declaration: {
    role: 'guide_vocal', origin: 'ai_generated', provider: 'Suno', original_filename: 'guide_vocal.wav',
    evidence_ref: 'new-agreement', permission_scope: 'Evaluation reference only under new terms.',
    acquired_at: '2026-10-01', source_artifact: 'original-source-id',
  } })
  expect(container.textContent).toContain('Evidence revisions: 2')
  expect(container.textContent).toContain(artistProductionEn.referenceOnly)
  expect(container.textContent).toContain(artistProductionEn.rightsStatus.unknown)
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
})
it('requires another rights review after revising an approved human source', async () => {
  const human = asset('1'.repeat(32), 'guide_vocal')
  sources = [human]
  const originalFetch = fetchMock.getMockImplementation()
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/declarations') && init) return new Response(JSON.stringify(amended(human, init)))
    if (!originalFetch) throw new Error('Missing fixture fetch')
    return originalFetch(url, init)
  })
  const container = await mount()
  const guideOption = () => [...container.querySelectorAll('option')].find(option => option.value === human.id)
  expect(guideOption()?.disabled).toBe(false)
  button(container, 'Revise source evidence').click(); await settle()
  await input(container, 'Revision reviewer label', 'Local operator')
  await input(container, 'Revised evidence reference', 'new-evidence')
  button(container, 'Append evidence revision').click(); await settle()
  expect(guideOption()?.disabled).toBe(true)
  expect(container.textContent).toContain('Each evidence revision requires a new rights review. Prior declarations and reviews remain recorded.')
})
it('ignores a late amendment response after changing voice sessions', async () => {
  const source = asset('1'.repeat(32), 'guide_vocal')
  sources = [source]
  let release: (response: Response) => void = () => { throw new Error('Missing deferred amendment') }
  let request: RequestInit | undefined
  const originalFetch = fetchMock.getMockImplementation()
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/declarations')) { request = init; return new Promise<Response>(resolve => { release = resolve }) }
    if (!originalFetch) throw new Error('Missing fixture fetch')
    return originalFetch(url, init)
  })
  const selected = ref<VoiceProfileResponse>(voice)
  const container = await mount(selected)
  button(container, 'Revise source evidence').click(); await settle()
  await input(container, 'Revision reviewer label', 'Local operator')
  await input(container, 'Revised evidence reference', 'late-evidence')
  button(container, 'Append evidence revision').click(); await settle()
  sources = []; profiles = []
  selected.value = { ...voice, id: '9'.repeat(32) }; await settle()
  if (!request) throw new Error('Missing amendment request')
  expect(request.signal?.aborted).toBe(true)
  release(new Response(JSON.stringify(amended(source, request)))); await settle()
  expect(container.textContent).not.toContain('guide_vocal.wav')
  expect(container.querySelector('[data-source-revision]')).toBeNull()
  expect(container.querySelector('[role=status]')?.textContent).not.toBe(artistProductionEn.saved)
})
it('binds a rights review to the displayed declaration revision and reports stale evidence', async () => {
  const source = asset('1'.repeat(32), 'guide_vocal')
  if (!source.declaration) throw new Error('Missing declaration fixture')
  sources = [amended(source, { body: JSON.stringify({ declaration: source.declaration, reviewer_label: 'Revision operator' }) })]
  const originalFetch = fetchMock.getMockImplementation()
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/reviews')) return new Response(JSON.stringify({ detail: 'source_evidence_changed' }), { status: 409 })
    if (!originalFetch) throw new Error('Missing fixture fetch')
    return originalFetch(url, init)
  })
  const container = await mount()
  await input(container, artistProductionEn.sourceReviewer, 'Rights reviewer')
  await input(container, artistProductionEn.reviewEvidence, 'reviewed-agreement')
  button(container, artistProductionEn.review).click(); await settle()
  const call = fetchMock.mock.calls.find(call => call[0].endsWith('/reviews'))
  const body: unknown = JSON.parse(String(call?.[1]?.body))
  expect(body).toMatchObject({ declaration_revision_id: '7'.repeat(32), reviewer_label: 'Rights reviewer', evidence_ref: 'reviewed-agreement' })
  expect(container.querySelector('[role=alert]')?.textContent).toContain('Source evidence changed. Refresh and review its current declaration before approving it.')
  expect(container.textContent).toContain(artistProductionEn.rightsStatus.unknown)
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
})
it('does not display an old approved review as approval for a new declaration', async () => {
  const source = asset('1'.repeat(32), 'guide_vocal')
  if (!source.declaration) throw new Error('Missing declaration fixture')
  const revised = amended(source, { body: JSON.stringify({ declaration: source.declaration, reviewer_label: 'Revision operator' }) })
  revised.reviews = source.reviews
  sources = [revised]
  const container = await mount()
  const guide = [...container.querySelectorAll('option')].find(option => option.value === source.id)
  expect(guide?.disabled).toBe(true)
  expect(container.querySelector('[data-source-review-status]')?.textContent).toBe(artistProductionEn.rightsStatus.unknown)
})
it('records normalized named operators as consent evidence without authenticating them', async () => {
  const originalFetch = fetchMock.getMockImplementation()
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/consents') && init?.method === 'POST') {
      const request = parseArtistConsentRequest(JSON.parse(String(init.body)))
      return new Response(JSON.stringify({ ...consent, ...request, id: 'f'.repeat(32), revision: 2 }))
    }
    if (!originalFetch) throw new Error('Missing fixture fetch')
    return originalFetch(url, init)
  })
  const container = await mount()
  await input(container, artistProductionEn.operator, 'Recording operator')
  await input(container, artistProductionEn.declaration, 'The signed agreement limits operation to the named people.')
  await input(container, artistProductionEn.evidence, 'signed-named-operators-grant')
  await input(container, 'Authorized operator labels (comma separated, optional)', ' Named    operator, , Second operator, Ｎａｍｅｄ operator ')
  labeledControl(container, artistProductionEn.operation.convert).click()
  labeledControl(container, artistProductionEn.use.production).click(); await settle()
  button(container, artistProductionEn.append).click(); await settle()
  const request = fetchMock.mock.calls.find(call => call[0].endsWith('/consents') && call[1]?.method === 'POST')
  const body: unknown = JSON.parse(String(request?.[1]?.body))
  expect(body).toMatchObject({ authorized_operators: ['Named operator', 'Second operator'], operator_label: 'Recording operator' })
  expect(container.textContent).toContain('Authorized operators recorded in agreement: Named operator, Second operator')
  expect(container.textContent).toContain('Named restrictions cannot be enforced without authenticated operator identities. Training, conversion and export remain blocked for this agreement.')
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
})
it('blocks a named-operator restriction even after entering a matching label and guide confirmations', async () => {
  consents = [{ ...consent, authorized_operators: ['Named operator'] }]
  const guide = asset('1'.repeat(32), 'guide_vocal'), accompaniment = { ...asset('2'.repeat(32), 'accompaniment'), sha256: 'e'.repeat(64) }
  sources = [guide, accompaniment]
  const container = await mount()
  await choose(container, artistProductionEn.guide, guide.id)
  await choose(container, artistProductionEn.accompaniment, accompaniment.id)
  await choose(container, artistProductionEn.renderTitle, 'Fixture master')
  labeledControl(container, artistProductionEn.isolated).click()
  labeledControl(container, artistProductionEn.singer).click(); await settle()
  await input(container, artistProductionEn.operator, 'Named operator')
  expect(button(container, artistProductionEn.render).disabled).toBe(true)
  button(container, artistProductionEn.render).click(); await settle()
  expect(fetchMock.mock.calls.some(call => call[0].endsWith('/runs') && call[1]?.method === 'POST')).toBe(false)
  expect(container.textContent).toContain('Named restrictions cannot be enforced without authenticated operator identities. Training, conversion and export remain blocked for this agreement.')
  expect(container.textContent).toContain('Leave the list empty only if the signed agreement permits the current local operators. Keep any genuine named restriction recorded.')
})
