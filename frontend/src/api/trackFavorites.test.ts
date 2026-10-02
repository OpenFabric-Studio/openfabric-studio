import { afterEach, expect, it, vi } from 'vitest'
import { listTracks, setTrackFavorite } from './tracks'

afterEach(() => vi.unstubAllGlobals())

const track = {
  id: 42, short_id: 42, model: 'upload', created_at: '2026-10-01T12:00:00Z',
  title: 'Song', lyrics: '', seed: null, duration_ms: 2000, wall_ms: null,
  params: {}, filename: 'song.wav', audio_url: '/api/tracks/42/audio',
  abc_url: null, stems: null, midi: null, is_favorite: true,
}

function respond(payload: unknown) {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

it('sends a validated favorite state and decodes the updated track with its abort signal', async () => {
  const fetch = respond(track)
  const signal = new AbortController().signal
  expect((await setTrackFavorite(42, true, signal)).is_favorite).toBe(true)
  expect(fetch).toHaveBeenCalledWith('/api/tracks/42/favorite', expect.objectContaining({
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_favorite: true }), signal,
  }))
})

it('rejects malformed favorite response flags at the wire boundary', async () => {
  respond({ ...track, is_favorite: 1 })
  await expect(setTrackFavorite(42, true)).rejects.toThrow()
})

it('rejects unsafe track identifiers and non-boolean input before sending a request', async () => {
  const fetch = respond(track)
  for (const id of [0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    await expect(setTrackFavorite(id, true)).rejects.toThrow()
  }
  await expect(Reflect.apply(setTrackFavorite, undefined, [42, 'true'])).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('forwards library refresh cancellation and preserves favorite values from the server', async () => {
  const fetch = respond({ data: [track] })
  const signal = new AbortController().signal
  expect((await listTracks('upload', signal))[0]?.is_favorite).toBe(true)
  expect(fetch).toHaveBeenCalledWith('/api/tracks?model=upload', expect.objectContaining({ signal }))
})
