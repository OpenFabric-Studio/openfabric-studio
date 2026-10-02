// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { listVoices, applyStatus } from './voices'
import { listVideos } from './videos'
import { getProject } from './projects'
import { getMixSettings } from './mix'
import { getMidiStatus } from './midi'
import { getSeparationStatus, startSeparation } from './stems'
import { getStatus } from './orchestrator'
import { uploadDatasetFiles } from './loraDataset'
import { defaultChannelSettings, defaultMasterSettings } from '../audio/mixerEngine'

afterEach(() => vi.unstubAllGlobals())

function respond(value: unknown): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(value))))
}

describe('app response validation', () => {
  it('forwards polling cancellation to voice, stems, and MIDI reads', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn().mockRejectedValue(new DOMException('Aborted', 'AbortError'))
    vi.stubGlobal('fetch', fetcher)
    await expect(listVoices(controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    await expect(getSeparationStatus(1, controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    await expect(getMidiStatus(1, controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/voices', { signal: controller.signal })
    expect(fetcher).toHaveBeenNthCalledWith(2, '/api/tracks/1/stems/status', { signal: controller.signal })
    expect(fetcher).toHaveBeenNthCalledWith(3, '/api/tracks/1/midi/status', { signal: controller.signal })
  })

  it('rejects malformed voice profiles before they enter stores', async () => {
    respond({ voices: [{ id: 'a', status: 'invented' }] })
    await expect(listVoices()).rejects.toThrow()
  })

  it('rejects an unknown voice conversion status', async () => {
    respond({ status: 'invented', error: '', error_code: '', audio_url: '' })
    await expect(applyStatus(1)).rejects.toThrow()
  })

  it('rejects an incomplete video job', async () => {
    respond({ videos: [{ id: 'a', status: 'ready' }] })
    await expect(listVideos()).rejects.toThrow()
  })

  it('rejects MIDI responses missing known source states', async () => {
    respond({ sources: {}, urls: {}, available: ['full'] })
    await expect(getMidiStatus(1)).rejects.toThrow()
  })

  it('accepts the start-stems envelope that omits download URLs', async () => {
    respond({ status: 'queued', error: null })
    await expect(startSeparation(1)).resolves.toEqual({ status: 'queued', error: null, stems: null })
  })

  it('rejects project documents that are not timeline projects', async () => {
    respond({ id: 1, name: 'project', created_at: '', updated_at: '', data: { version: 1, lanes: [] } })
    await expect(getProject(1)).rejects.toThrow()
  })

  it('rejects invalid nested clip timing and MIDI notes', async () => {
    respond({ id: 1, name: 'project', created_at: '', updated_at: '', data: {
      version: 1, master: defaultMasterSettings(), pxPerSecond: 40, bpm: 120, snapEnabled: true,
      lanes: [{ id: 'lane', name: 'MIDI', settings: defaultChannelSettings(), clips: [{
        id: 'clip', type: 'midi', sourceLabel: 'MIDI', timelineStart: 0, trimStart: 0, trimEnd: 1,
        notes: [{ note: 200, channel: 0, velocity: 1, startSec: 0, durationSec: 1 }],
      }] }],
    } })
    await expect(getProject(1)).rejects.toThrow()
  })

  it('rejects unstable feedback settings before creating an audio graph', async () => {
    const settings = defaultMasterSettings()
    respond({ settings: {
      version: 1, master: { ...settings, delay: { ...settings.delay, feedback: 2 } },
      stems: { vocals: defaultChannelSettings(), drums: defaultChannelSettings(), bass: defaultChannelSettings(), other: defaultChannelSettings() },
    } })
    await expect(getMixSettings(1)).rejects.toThrow()
  })

  it('rejects mix settings with incomplete channel controls', async () => {
    respond({ settings: { version: 1, stems: {}, master: {} } })
    await expect(getMixSettings(1)).rejects.toThrow()
  })

  it('rejects orchestrator responses missing a configured model', async () => {
    respond({ active_model: 'ace_step', models: {} })
    await expect(getStatus()).rejects.toThrow()
  })

  it('rejects upload envelopes containing non-string filenames', async () => {
    respond({ audio_dir: 'datasets/test', saved: [1], skipped: [] })
    await expect(uploadDatasetFiles('test', [])).rejects.toThrow()
  })
})
