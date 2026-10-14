// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import SingerRangePanel from './SingerRangePanel.vue'
import { singerRangeEn } from '../../locales/singerRange'
import en from '../../locales/en'
import type { RangeAssessment, SingerRangeProfile } from '../../api/contracts'

const artistId = 'a'.repeat(32)
const profile: SingerRangeProfile = {
  id: 'b'.repeat(32), artist_id: artistId, revision: 1, created_at: 'now',
  comfortable_low_midi: 60, comfortable_high_midi: 72, extended_low_midi: 57, extended_high_midi: 76,
  policy: 'needs_approval', chosen_key: 'C major', operator_label: 'Local operator',
  operator_declaration: 'I recorded the range confirmed by the artist.',
  artist_confirmation_declaration: 'The artist explicitly confirmed these comfortable and extended notes.',
  authenticated_actor: null, inferred_from_pitch: false,
}
const assessment: RangeAssessment = {
  artist_id: artistId, profile_version_id: profile.id, policy: 'needs_approval',
  analysis_status: 'observed', decision: 'needs_approval', assessment_sha256: 'c'.repeat(64),
  notes_evaluated: 1, chosen_key: 'C major', audio_transposed: false, approval_applied: false,
  out_of_range_notes: [{ start_sec: 0, end_sec: 1, midi_note: 81, distance_semitones: 9, outside_extended_range: true }],
  warnings: ['out_of_comfortable_range', 'approval_required'],
}
let app: App | undefined
let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/range/assess')) return new Response(JSON.stringify(assessment))
    if (url.endsWith('/range')) return new Response(JSON.stringify(init?.method === 'POST' ? { ...profile, revision: 2 } : profile))
    throw new Error('Unexpected fixture route')
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })
async function settle() { for (let index = 0; index < 12; index++) { await Promise.resolve(); await nextTick() } }
async function mount(selected = ref(artistId), active = ref(true)) {
  app = createApp({ render: () => h(SingerRangePanel, { artistId: selected.value, active: active.value }) })
  app.use(createI18n({ legacy: false, locale: 'en', messages: { en: { ...en, singerRange: singerRangeEn } } }))
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

it('shows the confirmed version, note names and limits of pitch screening', async () => {
  const container = await mount()
  expect(container.textContent).toContain(profile.id)
  expect(container.textContent).toContain('C4')
  expect(container.textContent).toContain(singerRangeEn.immutable)
  expect(container.textContent).toContain(singerRangeEn.observationHint)
  expect(container.textContent).toContain(singerRangeEn.actorHint)
})

it('validates reversed MIDI bounds and focuses a linked error summary', async () => {
  const container = await mount()
  await input(container, singerRangeEn.comfortableLow, '90')
  button(container, singerRangeEn.save).click(); await settle()
  expect(fetchMock.mock.calls.some(call => call[1]?.method === 'POST')).toBe(false)
  const error = container.querySelector('[role="alert"]')
  expect(error?.textContent).toContain(singerRangeEn.invalidBounds)
  expect(document.activeElement).toBe(error)
  expect(error?.querySelector('a')?.getAttribute('href')).toBeTruthy()
})

it('saves a new confirmed revision without adding authenticated identity', async () => {
  const container = await mount()
  await input(container, singerRangeEn.comfortableLow, '59')
  button(container, singerRangeEn.save).click(); await settle()
  const request = fetchMock.mock.calls.find(call => call[1]?.method === 'POST')?.[1]
  const body: unknown = JSON.parse(String(request?.body))
  expect(body).toMatchObject({ comfortable_low_midi: 59, comfortable_high_midi: 72, policy: 'needs_approval' })
  expect(body).not.toHaveProperty('authenticated_actor')
  expect(container.textContent).toContain(singerRangeEn.saved)
})

it('assesses one explicit planned note and binds an approval to its exact assessment', async () => {
  const container = await mount()
  await input(container, singerRangeEn.plannedNote, '81')
  button(container, singerRangeEn.assess).click(); await settle()
  expect(container.textContent).toContain(singerRangeEn.decisions.needs_approval)
  await input(container, singerRangeEn.approvalEvidence, 'artist-listening-review')
  await input(container, singerRangeEn.approvalDeclaration, 'The artist reviewed this exact note and confirms permission to proceed.')
  button(container, singerRangeEn.approve).click(); await settle()
  const calls = fetchMock.mock.calls.filter(call => call[0].endsWith('/range/assess'))
  const body: unknown = JSON.parse(String(calls.at(-1)?.[1]?.body))
  expect(body).toMatchObject({ notes: [{ start_sec: 0, end_sec: 1, midi_note: 81 }],
    approval: { profile_version_id: profile.id, assessment_sha256: assessment.assessment_sha256, evidence_ref: 'artist-listening-review' } })
})

it('ignores an old artist load after selection changes', async () => {
  let release: (response: Response) => void = () => { throw new Error('Not initialized') }
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
  const selected = ref(artistId)
  const container = await mount(selected)
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ...profile, id: 'd'.repeat(32), artist_id: 'e'.repeat(32), chosen_key: 'D major' })))
  selected.value = 'e'.repeat(32); await settle()
  release(new Response(JSON.stringify(profile))); await settle()
  expect(container.textContent).toContain('d'.repeat(32))
  expect(container.textContent).not.toContain(profile.id)
})

it('sanitizes a failed load and keeps actions blocked', async () => {
  fetchMock.mockRejectedValueOnce(new Error('/private/library/secret.wav'))
  const container = await mount()
  expect(container.textContent).toContain(singerRangeEn.errors.unknown)
  expect(container.textContent).not.toContain('/private/')
  expect(button(container, singerRangeEn.save).disabled).toBe(true)
  expect(button(container, singerRangeEn.assess).disabled).toBe(true)
})

it('aborts an outstanding assessment when the panel is deactivated', async () => {
  const active = ref(true)
  const container = await mount(ref(artistId), active)
  let release: (response: Response) => void = () => { throw new Error('Not initialized') }
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
  button(container, singerRangeEn.assess).click(); await settle()
  const request = fetchMock.mock.calls.at(-1)?.[1]
  active.value = false; await settle()
  expect(request?.signal?.aborted).toBe(true)
  release(new Response(JSON.stringify(assessment))); await settle()
  expect(container.textContent).not.toContain(singerRangeEn.decisions.needs_approval)
})

it('does not load inactive panels', async () => {
  await mount(ref(artistId), ref(false))
  expect(fetchMock).not.toHaveBeenCalled()
})
