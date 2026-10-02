import { describe, expect, it } from 'vitest'
import { parseAceJobResponse, parseSavedTrack, parseTracksResponse } from './contracts'

const track = {
  id: 1, short_id: 1, model: 'ace_step', created_at: '2026-10-01T00:00:00Z', title: 'Song', lyrics: '',
  seed: null, duration_ms: 10, wall_ms: null, params: { nested: [{ valid: true }, null] }, filename: 'a.wav',
  audio_url: '/api/tracks/1/audio', abc_url: null, stems: null, midi: null,
}
const job = {
  task_id: 'job', status: 'done', created_at: track.created_at, title: track.title, lyrics: '',
  audio_format: 'wav', batch_size: 2, params: {}, progress: 1, stage: 'done', error: '', error_code: '',
  tracks: [track], voice_id: null,
}

describe('generated contract validation', () => {
  it('accepts a real track envelope and recursive JSON parameters', () => {
    expect(parseTracksResponse({ data: [track] }).data[0]?.params).toEqual(track.params)
    expect(parseAceJobResponse(job).tracks).toHaveLength(1)
  })
  it.each([
    { ...track, id: '1' }, { ...track, model: 'unknown' }, { ...track, duration_ms: Infinity },
    { ...track, params: { nested: [undefined] } }, { ...track, stems: { vocals: 3 } },
  ])('rejects malformed track data', (malformed) => { expect(() => parseSavedTrack(malformed)).toThrow(TypeError) })
  it.each([
    { ...job, status: 'mystery' }, { ...job, progress: 1.1 }, { ...job, batch_size: 0 },
    { ...job, batch_size: 1.5 }, { ...job, tracks: [{ ...track, seed: '2' }] },
  ])('rejects malformed batch data', (malformed) => { expect(() => parseAceJobResponse(malformed)).toThrow(TypeError) })
  it('requires nullable fields to actually be present', () => {
    const { short_id: omitted, ...missing } = track
    expect(omitted).toBe(1)
    expect(() => parseSavedTrack(missing)).toThrow(TypeError)
  })
})
