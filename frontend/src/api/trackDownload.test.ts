import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadTrackAudio, taggedTrackDownloadUrl } from './trackDownload'

const version = 'a'.repeat(32)
const exported = 'b'.repeat(32)
afterEach(() => vi.unstubAllGlobals())

describe('tagged track download boundary', () => {
  it('requires exact version and export IDs and validates options before fetch', () => {
    expect(taggedTrackDownloadUrl(7, version)).toBe(`/api/tracks/7/versions/${version}/download`)
    expect(taggedTrackDownloadUrl(7, version, exported, { album: 'Àlbum 世界', track_no: 7 })).toBe(`/api/tracks/7/versions/${version}/exports/${exported}/download?album=%C3%80lbum+%E4%B8%96%E7%95%8C&track_no=7`)
    expect(() => taggedTrackDownloadUrl(0, version)).toThrow()
    expect(() => taggedTrackDownloadUrl(1, '../escape')).toThrow()
    expect(() => taggedTrackDownloadUrl(1, version, exported, { album: 'x'.repeat(121) })).toThrow()
  })

  it('returns the selected audio and decodes a bounded Unicode download name', async () => {
    const signal = new AbortController().signal
    const fetcher = vi.fn().mockResolvedValue(new Response(new Blob(['selected']), {
      headers: { 'Content-Disposition': "attachment; filename*=utf-8''Bj%C3%B6rk%20%E6%9D%B1%E4%BA%AC.flac" },
    }))
    vi.stubGlobal('fetch', fetcher)
    const downloaded = await downloadTrackAudio(7, version, exported, undefined, signal)
    expect(downloaded.filename).toBe('Björk 東京.flac')
    expect(await downloaded.blob.text()).toBe('selected')
    expect(fetcher).toHaveBeenCalledWith(`/api/tracks/7/versions/${version}/exports/${exported}/download`, { signal })
  })

  it('surfaces structured tagging failures and rejects empty audio', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'ffmpeg_missing' }), { status: 503 }))
      .mockResolvedValueOnce(new Response(new Blob([])))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { private: 'secret' } }), { status: 503 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(downloadTrackAudio(7, version)).rejects.toThrow('ffmpeg_missing')
    await expect(downloadTrackAudio(7, version)).rejects.toThrow('download_empty')
    await expect(downloadTrackAudio(7, version)).rejects.toThrow('download_failed')
  })
})
