// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n, setLocale } from '../../i18n'
import { downloadTrackAudio } from '../../api/trackDownload'
import TaggedAudioDownload from './TaggedAudioDownload.vue'

vi.mock('../../api/trackDownload', () => ({ downloadTrackAudio: vi.fn() }))
let app: App | undefined
const version = ref('1'.repeat(32))
beforeEach(() => {
  vi.clearAllMocks(); setLocale('en'); version.value = '1'.repeat(32)
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:download-test')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.restoreAllMocks(); vi.useRealTimers() })
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
function mount() {
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(TaggedAudioDownload, { trackId: 42, versionId: version.value }) }).use(i18n)
  app.mount(node); return node
}
function button(node: HTMLElement) { const result = node.querySelector('button'); if (!result) throw new Error('Missing download'); return result }

it('keeps internal failures private and supports an explicit retry', async () => {
  vi.mocked(downloadTrackAudio).mockRejectedValueOnce(new Error('/private/library/source.mp3'))
  const node = mount(); button(node).click(); await settle()
  expect(node.querySelector('[role=alert]')?.textContent).toContain('Could not prepare the tagged download')
  expect(node.textContent).not.toContain('/private/library'); expect(button(node).disabled).toBe(false)
  vi.mocked(downloadTrackAudio).mockResolvedValue({ blob: new Blob(['audio']), filename: 'selected.mp3' })
  button(node).click(); await settle()
  expect(node.querySelector('[role=alert]')).toBeNull()
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
})

it('aborts old selection and ignores a late download without creating a URL', async () => {
  let finish: (value: { blob: Blob; filename: string }) => void = () => { throw new Error('Missing request') }
  vi.mocked(downloadTrackAudio).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const node = mount(); button(node).click(); await settle()
  const signal = vi.mocked(downloadTrackAudio).mock.calls[0]?.[4]
  expect(signal?.aborted).toBe(false)
  version.value = '2'.repeat(32); await settle(); expect(signal?.aborted).toBe(true)
  finish({ blob: new Blob(['old']), filename: 'old.wav' }); await settle()
  expect(URL.createObjectURL).not.toHaveBeenCalled(); expect(button(node).disabled).toBe(false)
})

it('aborts on teardown and revokes every published URL after its download', async () => {
  vi.useFakeTimers()
  vi.mocked(downloadTrackAudio).mockResolvedValue({ blob: new Blob(['audio']), filename: 'selected.flac' })
  const node = mount(); button(node).click(); await settle()
  const signal = vi.mocked(downloadTrackAudio).mock.calls[0]?.[4]
  expect(URL.createObjectURL).toHaveBeenCalledOnce(); expect(document.querySelector('a[download]')).toBeNull()
  await vi.advanceTimersByTimeAsync(1000)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:download-test')
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0)
})
