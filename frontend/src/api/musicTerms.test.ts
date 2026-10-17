import { afterEach, expect, it, vi } from 'vitest'
import { acceptMusicTerms, getMusicTerms, updateMusicTermsSettings } from './musicTerms'

const digest = 'a'.repeat(64)
const snapshot = {
  engine: 'yue2', operation: 'generation', revision: 2, noncommercial_enabled: false, allowed: false,
  requirements: [{ component_id: 'yue-weights', name: 'YuE weights', scope: 'model', restriction: 'noncommercial', source_url: 'https://example.org/terms', terms_digest: digest, acknowledged: false }],
}
afterEach(() => vi.restoreAllMocks())

it('loads and validates operation-specific terms without triggering model work', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(snapshot)))
  const signal = new AbortController().signal
  await expect(getMusicTerms('yue2', 'generation', signal)).resolves.toEqual(snapshot)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music-terms?engine=yue2&operation=generation', { signal })
})

it('validates explicit acceptance before submitting the current revision and digests', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(snapshot)))
  const signal = new AbortController().signal
  const body = { engine: 'yue2', operation: 'generation', expected_revision: 2, terms_digests: [digest], allow_noncommercial: true }
  await acceptMusicTerms({ ...body, engine: 'yue2', operation: 'generation' }, signal)
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music-terms/accept', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  expect(() => acceptMusicTerms({ engine: 'yue2', operation: 'generation', expected_revision: -1, terms_digests: ['invalid'], allow_noncommercial: true })).toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('updates the global choice with revision checking and rejects malformed responses', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ revision: 3, noncommercial_enabled: false }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...snapshot, requirements: [{ ...snapshot.requirements[0], terms_digest: 'invalid' }] })))
  const body = { expected_revision: 2, noncommercial_enabled: false }
  await expect(updateMusicTermsSettings(body)).resolves.toEqual({ revision: 3, noncommercial_enabled: false })
  expect(fetch).toHaveBeenNthCalledWith(1, '/api/music-terms/settings', { method: 'PATCH', signal: undefined, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  await expect(getMusicTerms('yue2', 'generation')).rejects.toThrow()
})

it('rejects a well-formed response for a different workflow', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...snapshot, engine: 'ace_step' })))
  await expect(getMusicTerms('yue2', 'generation')).rejects.toThrow()
})
