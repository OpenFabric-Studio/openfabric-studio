// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, KeepAlive, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import { ApiError } from '../../api/http'
import { uploadTrack, type SavedTrack } from '../../api/tracks'
import TrainingAudioImport from './TrainingAudioImport.vue'

vi.mock('../../api/tracks', () => ({ uploadTrack: vi.fn() }))
let app: App | undefined
const recording: SavedTrack = { id: 51, short_id: 51, model: 'upload', title: 'Verse', created_at: 'now', lyrics: '', seed: null, duration_ms: 12000, wall_ms: null, params: {}, filename: 'verse.wav', audio_url: '/api/tracks/51/audio', abc_url: null, stems: null, midi: null }
async function settle() { for (let i = 0; i < 30; i++) await nextTick() }
async function mount(capacity = 100) {
  const selected = ref<number[]>([]), picks: Array<{ trackId: number; sourceLabel: string }> = [], busy = vi.fn()
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TrainingAudioImport, { limit: capacity - selected.value.length, selectedTrackIds: selected.value, onBusy: busy, onPick: value => { picks.push(value); selected.value.push(value.trackId) } }) }).use(i18n)
  app.mount(node); await settle()
  return { node, selected, picks, busy }
}
function button(node: ParentNode, label: string) {
  const result = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!result) throw new Error(`Missing ${label}`)
  return result
}
async function choose(node: ParentNode, files: File[], folder = false) {
  const control = node.querySelector(`[name="training-audio-${folder ? 'folder' : 'files'}"]`)
  if (!(control instanceof HTMLInputElement)) throw new Error('Missing audio picker')
  Object.defineProperty(control, 'files', { configurable: true, value: files })
  control.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
beforeEach(() => { vi.resetAllMocks(); vi.mocked(uploadTrack).mockResolvedValue(recording) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks() })

it('provides keyboard file and folder alternatives and filters unsupported recordings', async () => {
  const { node, picks } = await mount()
  const files = node.querySelector('[name="training-audio-files"]'), folder = node.querySelector('[name="training-audio-folder"]')
  expect(files).toBeInstanceOf(HTMLInputElement)
  expect(files?.hasAttribute('multiple')).toBe(true)
  expect(folder?.hasAttribute('webkitdirectory')).toBe(true)
  const click = vi.spyOn(HTMLInputElement.prototype, 'click')
  button(node, 'Choose files').click(); button(node, 'Choose folder').click()
  expect(click).toHaveBeenCalledTimes(2)
  await choose(node, [new File(['audio'], 'verse.WAV'), new File(['notes'], 'notes.txt'), new File([], 'empty.mp3')], true)
  expect(uploadTrack).toHaveBeenCalledTimes(1)
  expect(picks).toEqual([{ trackId: 51, sourceLabel: 'Verse' }])
  expect(node.textContent).toContain('Added 1 track')
  expect(node.textContent).toContain('Skipped 2 unsupported or empty files')
})

it('uploads sequentially and respects the remaining training capacity', async () => {
  const { node, picks, busy } = await mount(1)
  let finish: ((value: SavedTrack) => void) | undefined
  vi.mocked(uploadTrack).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await choose(node, [new File(['verse'], 'verse.wav'), new File(['chorus'], 'chorus.flac')])
  expect(uploadTrack).toHaveBeenCalledTimes(1)
  expect(busy).toHaveBeenLastCalledWith(true)
  expect(node.textContent).toContain('Uploading 1 of 2')
  if (!finish) throw new Error('Missing upload resolver')
  finish(recording); await settle()
  expect(uploadTrack).toHaveBeenCalledTimes(1)
  expect(picks).toHaveLength(1)
  expect(node.textContent).toContain('1 file was not added because the training selection is full')
  expect(busy).toHaveBeenLastCalledWith(false)
})

it('adds several files dropped onto the training import area', async () => {
  const { node, picks } = await mount()
  const transfer = new DataTransfer()
  transfer.items.add(new File(['verse'], 'verse.wav'))
  transfer.items.add(new File(['chorus'], 'chorus.mp3'))
  vi.mocked(uploadTrack).mockResolvedValueOnce(recording).mockResolvedValueOnce({ ...recording, id: 52, title: 'Chorus' })
  const event = new Event('drop', { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'dataTransfer', { value: transfer })
  const area = node.querySelector('section[aria-label="Drop audio files or a folder here"]')
  if (!area) throw new Error('Missing training drop area')
  area.dispatchEvent(event); await settle()
  expect(event.defaultPrevented).toBe(true)
  expect(uploadTrack).toHaveBeenCalledTimes(2)
  expect(picks.map(track => track.trackId)).toEqual([51, 52])
})

it('does not upload an already selected file again and reuses its library copy after removal', async () => {
  const { node, picks, selected } = await mount()
  const file = new File(['verse'], 'verse.wav', { lastModified: 123 })
  await choose(node, [file, file])
  expect(uploadTrack).toHaveBeenCalledTimes(1)
  expect(node.textContent).toContain('Skipped 1 already selected file')
  await choose(node, [file])
  expect(uploadTrack).toHaveBeenCalledTimes(1)
  expect(picks).toHaveLength(1)
  selected.value = []; await settle()
  await choose(node, [file])
  expect(uploadTrack).toHaveBeenCalledTimes(1)
  expect(picks).toHaveLength(2)
})

it('imports distinct recordings even when their names, sizes and timestamps match', async () => {
  const { node, picks, selected } = await mount()
  const first = new File(['take-a'], 'take.wav', { lastModified: 123 }), second = new File(['take-b'], 'take.wav', { lastModified: 123 })
  vi.mocked(uploadTrack).mockResolvedValueOnce(recording).mockResolvedValueOnce({ ...recording, id: 52, title: 'Second take' })
  await choose(node, [first, second])
  expect(uploadTrack).toHaveBeenCalledTimes(2)
  expect(picks.map(track => track.trackId)).toEqual([51, 52])
  selected.value = []; await settle()
  await choose(node, [second])
  expect(picks.at(-1)?.trackId).toBe(52)
})

it('reports a saved upload as unselected if another selection fills the remaining capacity', async () => {
  const { node, picks, selected } = await mount(1)
  let finish: ((value: SavedTrack) => void) | undefined
  vi.mocked(uploadTrack).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await choose(node, [new File(['a'], 'verse.wav')])
  selected.value = [99]; await settle()
  if (!finish) throw new Error('Missing upload resolver')
  finish(recording); await settle()
  expect(picks).toHaveLength(0)
  expect(node.textContent).toContain('Added 0 tracks')
  expect(node.textContent).toContain('1 file was not added because the training selection is full')
})

it('keeps successful files and continues after a rejected file without exposing internal errors', async () => {
  const { node, picks } = await mount()
  vi.mocked(uploadTrack).mockRejectedValueOnce(new ApiError('/private/internal-secret', 400)).mockResolvedValueOnce(recording)
  await choose(node, [new File(['bad'], 'broken.wav'), new File(['verse'], 'verse.wav')])
  expect(uploadTrack).toHaveBeenCalledTimes(2)
  expect(picks).toHaveLength(1)
  expect(node.textContent).toContain('broken.wav')
  expect(node.textContent).toContain('Check the library before uploading these files again')
  expect(node.textContent).not.toContain('internal-secret')
})

it('stops after an unconfirmed network failure and retains completed additions', async () => {
  const { node, picks } = await mount()
  vi.mocked(uploadTrack).mockResolvedValueOnce(recording).mockRejectedValueOnce(new TypeError('private network details'))
  await choose(node, [new File(['a'], 'verse.wav'), new File(['b'], 'lost.wav'), new File(['c'], 'later.wav')])
  expect(uploadTrack).toHaveBeenCalledTimes(2)
  expect(picks).toHaveLength(1)
  expect(node.textContent).toContain('Import stopped before all files were processed')
  expect(node.textContent).not.toContain('private network details')
})

it('cancels an in-flight upload and ignores its late result', async () => {
  const { node, picks, busy } = await mount()
  let finish: ((value: SavedTrack) => void) | undefined
  vi.mocked(uploadTrack).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await choose(node, [new File(['a'], 'verse.wav'), new File(['b'], 'chorus.wav')])
  const signal = vi.mocked(uploadTrack).mock.calls[0]?.[2]
  button(node, 'Cancel import').click(); await settle()
  expect(signal?.aborted).toBe(true)
  expect(busy).toHaveBeenLastCalledWith(false)
  if (!finish) throw new Error('Missing upload resolver')
  finish(recording); await settle()
  expect(picks).toHaveLength(0)
  expect(uploadTrack).toHaveBeenCalledTimes(1)
  expect(node.textContent).toContain('Import cancelled')
})

it('aborts imports on deactivation without adding a late result to a hidden model', async () => {
  const visible = ref(true), picks = vi.fn()
  let finish: ((value: SavedTrack) => void) | undefined
  vi.mocked(uploadTrack).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(KeepAlive, null, { default: () => visible.value ? h(TrainingAudioImport, { limit: 100, selectedTrackIds: [], onPick: picks }) : h('span', 'Other model') }) }).use(i18n)
  app.mount(node); await settle()
  await choose(node, [new File(['a'], 'verse.wav')])
  const signal = vi.mocked(uploadTrack).mock.calls[0]?.[2]
  visible.value = false; await settle()
  expect(signal?.aborted).toBe(true)
  if (!finish) throw new Error('Missing upload resolver')
  finish(recording); await settle()
  expect(picks).not.toHaveBeenCalled()
})
