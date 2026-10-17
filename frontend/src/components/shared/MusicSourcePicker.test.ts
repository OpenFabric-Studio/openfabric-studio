// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import * as tracks from '../../api/tracks'
import * as versions from '../../api/audioVersions'
import * as conditioning from '../../api/musicConditioning'
import type { MusicConditioningSourceInfo, SelectedSource } from '../../api/generated'
import MusicSourcePicker from './MusicSourcePicker.vue'

vi.mock('../../api/tracks', () => ({ listTracks: vi.fn(), uploadTrack: vi.fn() }))
vi.mock('../../api/audioVersions', () => ({ listAudioVersions: vi.fn() }))
vi.mock('../../api/musicConditioning', () => ({ inspectConditioningSource: vi.fn() }))
let app: App | undefined
const source = { kind: 'version', track_id: 42, version_id: 'a'.repeat(32) } as const
const info: MusicConditioningSourceInfo = { source, source_sha256: 'b'.repeat(64), source_bytes: 1000,
  source_rate_hz: 44100, source_channels: 2, source_frames: 220500, duration_sec: 5 }
async function settle() { for (let n = 0; n < 15; n++) await nextTick() }
function button(label: string) {
  const found = [...document.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!found) throw new Error(`Missing ${label}`)
  return found
}
async function mount(initial: SelectedSource | null = null) {
  const selected = ref<SelectedSource | null>(initial), busy = vi.fn()
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(MusicSourcePicker, { modelValue: selected.value, label: 'Vocal source',
    'onUpdate:modelValue': value => { selected.value = value }, onBusy: busy }) }).use(i18n)
  app.mount(node); await settle()
  return { node, selected, busy }
}
async function chooseVersion() {
  button('Choose library source').click(); await settle()
  button('Use as audio source').click(); await settle()
  const select = document.querySelector('select')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing saved-version selector')
  select.value = source.version_id; select.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(tracks.listTracks).mockResolvedValue([{ id: 42, short_id: 42, model: 'upload', title: 'Piano',
    created_at: 'now', lyrics: '', seed: null, duration_ms: 5000, wall_ms: null, params: {}, filename: 'piano.wav',
    audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null }])
  vi.mocked(versions.listAudioVersions).mockResolvedValue({ track_id: 42, original_available: true,
    versions: [{ id: source.version_id, track_id: 42, kind: 'original', status: 'done', created_at: 'now' },
      { id: 'c'.repeat(32), track_id: 42, kind: 'voice', status: 'running', created_at: 'now' }] })
  vi.mocked(conditioning.inspectConditioningSource).mockResolvedValue(info)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })

it('chooses a completed retained version and inspects before emitting a hash-bound source', async () => {
  const { selected, node } = await mount()
  await chooseVersion()
  expect(selected.value).toBeNull()
  expect(conditioning.inspectConditioningSource).not.toHaveBeenCalled()
  expect(node.querySelectorAll('option')).toHaveLength(2)
  button('Inspect source').click(); await settle()
  expect(conditioning.inspectConditioningSource).toHaveBeenCalledWith(source, expect.any(AbortSignal))
  expect(selected.value).toEqual({ source, audio_sha256: info.source_sha256 })
  expect(node.textContent).toContain('44100 Hz')
})

it('aborts inspection and drops late responses on teardown', async () => {
  let finish: ((value: MusicConditioningSourceInfo) => void) | undefined
  vi.mocked(conditioning.inspectConditioningSource).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { selected } = await mount(); await chooseVersion()
  button('Inspect source').click(); await settle()
  const signal = vi.mocked(conditioning.inspectConditioningSource).mock.calls[0]?.[1]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  finish?.(info); await settle()
  expect(selected.value).toBeNull()
})

it('retains no stale identity when another version is selected', async () => {
  const { selected } = await mount(); await chooseVersion()
  button('Inspect source').click(); await settle()
  expect(selected.value).not.toBeNull()
  const select = document.querySelector('select')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Missing saved-version selector')
  select.value = ''; select.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  expect(selected.value).toBeNull()
})

it('hydrates a saved source without clearing its identity or automatically rehashing', async () => {
  const saved: SelectedSource = { source, audio_sha256: info.source_sha256 }
  const { selected, node } = await mount(saved)
  expect(selected.value).toEqual(saved)
  expect(conditioning.inspectConditioningSource).not.toHaveBeenCalled()
  expect(versions.listAudioVersions).toHaveBeenCalledWith(42, expect.any(AbortSignal))
  expect(node.textContent).toContain(info.source_sha256)
  const selector = node.querySelector('select')
  expect(selector instanceof HTMLSelectElement && selector.value).toBe(source.version_id)
})

it('cancels plan A inspection when plan B supplies another saved source', async () => {
  let finish: ((value: MusicConditioningSourceInfo) => void) | undefined
  vi.mocked(conditioning.inspectConditioningSource).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { selected, node } = await mount({ source, audio_sha256: info.source_sha256 })
  button('Inspect source').click(); await settle()
  const signal = vi.mocked(conditioning.inspectConditioningSource).mock.calls[0]?.[1]
  const replacement: SelectedSource = { source: { ...source, version_id: 'c'.repeat(32) }, audio_sha256: 'd'.repeat(64) }
  vi.mocked(versions.listAudioVersions).mockResolvedValue({ track_id: 42, original_available: true,
    versions: [{ id: 'c'.repeat(32), track_id: 42, kind: 'voice', status: 'done', created_at: 'later' }] })
  selected.value = replacement; await settle()
  expect(signal?.aborted).toBe(true)
  finish?.(info); await settle()
  expect(selected.value).toEqual(replacement)
  expect(node.textContent).toContain(replacement.audio_sha256)
  expect(node.textContent).not.toContain(info.source_sha256)
})
