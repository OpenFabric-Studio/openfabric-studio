// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import * as api from './artistProduction'

const voiceId = 'a'.repeat(32), artistId = 'b'.repeat(32)
afterEach(() => vi.unstubAllGlobals())

it('validates profile responses and forwards cancellation', async () => {
  const profile = { id: artistId, name: 'Artist', voice_id: voiceId, created_at: 'now', current_consent_version_id: null }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ artists: [profile] })))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.listArtists(controller.signal)).toEqual([profile])
  expect(fetch).toHaveBeenCalledWith('/api/artists', { signal: controller.signal })
})

it('rejects forged authentication at the response boundary', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ artist_id: artistId, versions: [{
    id: 'c'.repeat(32), artist_id: artistId, revision: 1, created_at: 'now', status: 'needs_review', actor_authenticated: true,
  }] }))))
  await expect(api.listArtistConsents(artistId)).rejects.toThrow('Invalid ArtistConsentsResponse')
})

it('does not submit unsafe identities or malformed confirmation', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  await expect(api.listArtistConsents('../private')).rejects.toThrow()
  await expect(api.confirmVoiceDeletion(voiceId, { plan_sha256: 'bad', confirm_voice_id: voiceId, delete_voice_assets: true })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('uploads a separate source with a schema-validated declaration', async () => {
  const declaration = { role: 'guide_vocal', origin: 'human_recorded', provider: 'Artist', rights_holder: 'Artist', original_filename: 'guide.wav', evidence_ref: 'agreement-1', permission_scope: 'production', permitted_uses: ['production'] } satisfies Parameters<typeof api.uploadSourceAsset>[1]
  const source = { schema_version: 1, id: 'd'.repeat(32), sha256: 'e'.repeat(64), role: 'guide_vocal', origin: 'human_recorded', provider: 'Artist', created_at: 'now', declaration, classification_basis: 'user_declaration', reference_only: false, parent_ids: [], transformation: '', reviews: [] }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(source)))
  vi.stubGlobal('fetch', fetch)
  const audio = new File(['fixture'], 'guide.wav', { type: 'audio/wav' })
  await api.uploadSourceAsset(audio, declaration)
  const init: unknown = fetch.mock.calls[0]?.[1]
  expect(init).toBeDefined()
  const [, request] = fetch.mock.calls[0] ?? []
  if (!request || !(request.body instanceof FormData)) throw new Error('Missing multipart request')
  expect(request.body.get('audio')).toBeInstanceOf(File)
  expect(JSON.parse(String(request.body.get('declaration')))).toMatchObject(declaration)
})
