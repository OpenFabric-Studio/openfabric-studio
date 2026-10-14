// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import * as api from './singerRanges'

afterEach(() => vi.unstubAllGlobals())

it('accepts an absent range and forwards cancellation', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('null'))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  expect(await api.getSingerRange('a'.repeat(32), controller.signal)).toBeNull()
  expect(fetch).toHaveBeenCalledWith(`/api/artists/${'a'.repeat(32)}/range`, { signal: controller.signal })
})

it('rejects unsafe artist identities before making a request', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  await expect(api.getSingerRange('../private')).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('rejects an unvalidated planned note before submitting it', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  await expect(api.assessSingerRange('a'.repeat(32), { notes: [{ start_sec: 0, end_sec: 1, midi_note: 128 }] })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('rejects a validly shaped range response for a different artist', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    id: 'b'.repeat(32), artist_id: 'c'.repeat(32), revision: 1, created_at: 'now',
    comfortable_low_midi: 60, comfortable_high_midi: 72, operator_label: 'Operator',
    operator_declaration: 'I recorded the explicitly confirmed bounds.',
    artist_confirmation_declaration: 'The artist confirms these exact comfortable notes.',
  }))))
  await expect(api.getSingerRange('a'.repeat(32))).rejects.toThrow('Invalid artist range identity')
})
