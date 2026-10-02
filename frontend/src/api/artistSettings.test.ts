import { afterEach, describe, expect, it, vi } from 'vitest'
import { getArtistSettings, saveArtistSettings } from './artistSettings'

afterEach(() => vi.unstubAllGlobals())

describe('artist settings boundary', () => {
  it('loads and saves the generated contract with cancellation', async () => {
    const signal = new AbortController().signal
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ artist: 'Björk 東京' })))
    vi.stubGlobal('fetch', fetcher)
    expect(await getArtistSettings(signal)).toEqual({ artist: 'Björk 東京' })
    expect(await saveArtistSettings('Björk 東京', signal)).toEqual({ artist: 'Björk 東京' })
    expect(fetcher).toHaveBeenLastCalledWith('/api/settings', expect.objectContaining({ method: 'PUT', signal, body: JSON.stringify({ artist: 'Björk 東京' }) }))
  })

  it('rejects invalid input before sending and malformed responses', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ artist: 7 })))
    vi.stubGlobal('fetch', fetcher)
    await expect(saveArtistSettings('x'.repeat(121))).rejects.toThrow('Invalid ArtistSettings')
    expect(fetcher).not.toHaveBeenCalled()
    await expect(getArtistSettings()).rejects.toThrow('Invalid ArtistSettings')
  })
})
