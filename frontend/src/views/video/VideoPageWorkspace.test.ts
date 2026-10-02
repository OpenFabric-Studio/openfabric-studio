// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import VideoPage from './VideoPage.vue'
import * as api from '../../api/videos'
import * as tracks from '../../api/tracks'
import type { VideoProject } from '../../api/contracts'
import { videoProjectFixture, videoTrack, videoReadinessFixture } from './videoFixtures'

vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn() }))
vi.mock('../../api/videos', async (original) => ({ ...await original<typeof import('../../api/videos')>(), listVideoProjects: vi.fn(), getVideoProject: vi.fn(), deleteVideoProject: vi.fn(), listVideos: vi.fn(), otherWorkBusy: vi.fn(), videoReadiness: vi.fn(), createVideoProject: vi.fn(), updateVideoProject: vi.fn(), analyzeVideoProject: vi.fn(), previewVideoProject: vi.fn(), renderVideoProject: vi.fn(), exportVideoProject: vi.fn(), uploadVideoReference: vi.fn() }))
let app: App | undefined
let project: VideoProject
beforeEach(() => {
  vi.useFakeTimers()
  project = videoProjectFixture()
  vi.mocked(tracks.listTracks).mockResolvedValue([videoTrack])
  vi.mocked(api.listVideos).mockResolvedValue({ videos: [] })
  vi.mocked(api.otherWorkBusy).mockResolvedValue(false)
  vi.mocked(api.videoReadiness).mockResolvedValue(videoReadinessFixture)
  vi.mocked(api.listVideoProjects).mockImplementation(async () => ({ projects: [project] }))
  vi.mocked(api.getVideoProject).mockImplementation(async () => project)
  vi.mocked(api.updateVideoProject).mockImplementation(async (_id, body) => {
    project = { ...project, revision: project.revision + 1, name: body.name ?? project.name, direction: body.direction ?? project.direction,
      shots: body.shots?.map((shot) => ({ ...shot, variants: [], approved_variant_id: null })) ?? project.shots }
    return project
  })
  vi.mocked(api.previewVideoProject).mockImplementation(async () => project)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); sessionStorage.clear(); vi.clearAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })
async function flush() { for (let i = 0; i < 12; i++) await nextTick() }
async function mount() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: VideoPage }] })
  await router.push('/')
  app = createApp({ render: () => h(VideoPage) })
  app.use(createPinia()).use(i18n).use(router)
  app.mount(document.body.appendChild(document.createElement('div')))
  await flush()
}
function button(text: string): HTMLButtonElement {
  const match = [...document.querySelectorAll('button')].find((element) => element.getAttribute('aria-label') === text || element.textContent?.trim() === text)
  if (!match) throw new Error(`Missing button ${text}`)
  return match
}
it('shows the numbered project progression and an audio source player', async () => {
  await mount()
  expect(document.querySelectorAll('[role=tab]')).toHaveLength(5)
  expect(document.body.textContent).toContain('Storyboard')
  expect(document.querySelector('audio')?.getAttribute('src')).toBe('/api/tracks/1/audio')
})

it('toggles the project manager beside the selector and shows the library above the wizard', async () => {
  await mount()
  const manager = button('Manage projects')
  expect(manager.closest('header')).not.toBeNull()
  expect(manager.getAttribute('aria-expanded')).toBe('false')
  expect(document.querySelector('[data-video-project-library]')).toBeNull()
  manager.click(); await flush()
  expect(manager.getAttribute('aria-expanded')).toBe('true')
  const library = document.querySelector('[data-video-project-library]')
  const wizard = document.querySelector('[role=tablist]')
  if (!library || !wizard) throw new Error('Missing manager or wizard')
  expect(library.compareDocumentPosition(wizard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect([...document.querySelectorAll('details > summary')].some(element => element.textContent?.includes('Project library'))).toBe(false)
  manager.click(); await flush()
  expect(document.querySelector('[data-video-project-library]')).toBeNull()
})

it('deletes only the named confirmed project and keeps cancellation silent', async () => {
  const other = { ...videoProjectFixture('e'.repeat(32)), name: 'Other video' }
  vi.mocked(api.listVideoProjects).mockResolvedValue({ projects: [project, other] })
  vi.mocked(api.deleteVideoProject).mockResolvedValue(undefined)
  const confirm = vi.fn(() => false); vi.stubGlobal('confirm', confirm)
  await mount(); button('Manage projects').click(); await flush()
  const row = document.querySelector(`[data-video-project="${other.id}"]`)
  const remove = row?.querySelector<HTMLButtonElement>('[data-delete-project]')
  if (!remove) throw new Error('Missing project deletion')
  remove.click(); await flush()
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Other video'))
  expect(api.deleteVideoProject).not.toHaveBeenCalled()
  confirm.mockReturnValue(true); remove.click(); await flush()
  expect(api.deleteVideoProject).toHaveBeenCalledWith(other.id, expect.any(AbortSignal))
  expect(document.querySelector(`[data-video-project="${other.id}"]`)).toBeNull()
  expect(document.querySelector('[data-testid=video-global-status]')?.textContent).toContain(project.name)
})

it('shows a stable deletion failure and leaves its project available', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.mocked(api.deleteVideoProject).mockRejectedValue(new Error('/private/project/path'))
  await mount(); button('Manage projects').click(); await flush()
  document.querySelector<HTMLButtonElement>('[data-delete-project]')?.click(); await flush()
  expect(document.querySelector(`[data-video-project="${project.id}"]`)).not.toBeNull()
  expect([...document.querySelectorAll('[role=alert]')].map(element => element.textContent).join(' ')).toContain('Could not delete this project')
  expect(document.body.textContent).not.toContain('/private/project/path')
})

it('keeps the manager open with an empty message after deleting its last project', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.mocked(api.deleteVideoProject).mockResolvedValue(undefined)
  await mount(); button('3 Storyboard').click(); await flush()
  button('Manage projects').click(); await flush()
  document.querySelector<HTMLButtonElement>('[data-delete-project]')?.click(); await flush()
  expect(document.querySelector('[data-video-project-library]')).not.toBeNull()
  expect(document.body.textContent).toContain('No saved video projects yet')
  expect(document.querySelector('[role=tab][aria-selected=true]')?.textContent).toContain('Song')
  expect(document.querySelector('[data-testid=video-global-status]')?.textContent).toContain('Choose a song to begin')
  expect([...document.querySelectorAll('[role=tab]')].some(tab => tab.textContent?.includes('✓'))).toBe(false)
})

it('shows no completed wizard steps before a project exists', async () => {
  vi.mocked(api.listVideoProjects).mockResolvedValueOnce({ projects: [] })
  await mount()
  expect([...document.querySelectorAll('[role=tab]')].some(tab => tab.textContent?.includes('✓'))).toBe(false)
})

it('disables manager and row actions until a deletion settles', async () => {
  let finish: () => void = () => { throw new Error('Not initialized') }
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.mocked(api.deleteVideoProject).mockReturnValueOnce(new Promise<void>(resolve => { finish = resolve }))
  await mount(); const manager = button('Manage projects'); manager.click(); await flush()
  document.querySelector<HTMLButtonElement>('[data-delete-project]')?.click(); await flush()
  expect(manager.disabled).toBe(true)
  expect(document.querySelector<HTMLButtonElement>('[data-open-project]')?.disabled).toBe(true)
  expect(document.querySelector<HTMLButtonElement>('[data-delete-project]')?.disabled).toBe(true)
  finish(); await flush()
  expect(manager.disabled).toBe(false)
})
it('previews only the selected shot without rendering the whole song', async () => {
  await mount()
  button('3 Storyboard').click()
  await flush()
  button('Preview this shot').click()
  await flush()
  expect(api.previewVideoProject).toHaveBeenCalledWith(project.id, expect.objectContaining({ revision: 1, shot_ids: ['b'.repeat(32)], variants_per_shot: 1 }), expect.any(AbortSignal))
  expect(api.renderVideoProject).not.toHaveBeenCalled()
})
it('retains an edited prompt when navigating between project steps', async () => {
  await mount()
  button('3 Storyboard').click()
  await flush()
  const prompt = document.querySelector<HTMLTextAreaElement>('[data-testid=video-shot-prompt]')
  if (!prompt) throw new Error('Missing prompt editor')
  prompt.value = 'My revised scene'
  prompt.dispatchEvent(new Event('input', { bubbles: true }))
  await vi.advanceTimersByTimeAsync(700)
  await flush()
  button('2 Direction').click()
  await flush()
  button('3 Storyboard').click()
  await flush()
  expect(document.querySelector<HTMLTextAreaElement>('[data-testid=video-shot-prompt]')?.value).toBe('My revised scene')
  expect(api.updateVideoProject).toHaveBeenCalled()
})
it('keeps the active project status visible while moving between steps', async () => {
  project = { ...project, job: { id: 'd'.repeat(32), operation: 'preview', status: 'running', shot_ids: ['b'.repeat(32)], phase: 'denoise', shot_index: 1, shot_count: 1, progress_current: 5, progress_total: 30, started_at: '2026-10-01T10:00:00Z' } }
  await mount()
  const status = document.querySelector('[data-testid=video-global-status]')
  expect(status).not.toBeNull()
  button('2 Direction').click()
  await flush()
  expect(document.querySelector('[data-testid=video-global-status]')?.textContent).toContain('5 / 30')
})
it('does not replace a saved edit with a poll that started before the save', async () => {
  await mount()
  button('3 Storyboard').click()
  await flush()
  const old = structuredClone(project)
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.getVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  await vi.advanceTimersByTimeAsync(2000)
  const prompt = document.querySelector<HTMLTextAreaElement>('[data-testid=video-shot-prompt]')
  if (!prompt) throw new Error('Missing prompt')
  prompt.value = 'Newer saved scene'
  prompt.dispatchEvent(new Event('input', { bubbles: true }))
  await vi.advanceTimersByTimeAsync(700)
  await flush()
  expect(project.revision).toBe(2)
  release(old)
  await flush()
  expect(document.querySelector<HTMLTextAreaElement>('[data-testid=video-shot-prompt]')?.value).toBe('Newer saved scene')
})
it('admits only one preview while rapid clicks wait for an in-flight save', async () => {
  await mount()
  button('3 Storyboard').click()
  await flush()
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.updateVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const prompt = document.querySelector<HTMLTextAreaElement>('[data-testid=video-shot-prompt]')
  if (!prompt) throw new Error('Missing prompt')
  prompt.value = 'Saved first'
  prompt.dispatchEvent(new Event('input', { bubbles: true }))
  button('Preview this shot').click()
  button('Preview this shot').click()
  await flush()
  release({ ...project, revision: 2 })
  await flush()
  expect(api.previewVideoProject).toHaveBeenCalledTimes(1)
})
it('uses the new revision for further edits restored from the browser draft', async () => {
  sessionStorage.setItem(`openfabric:video-draft:${project.id}`, JSON.stringify({ revision: 1, name: project.name, shots: project.shots?.map(({ variants: _variants, approved_variant_id: _approval, ...shot }) => shot) }))
  await mount()
  button('3 Storyboard').click()
  await flush()
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.updateVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const prompt = document.querySelector<HTMLTextAreaElement>('[data-testid=video-shot-prompt]')
  if (!prompt) throw new Error('Missing prompt')
  prompt.value = 'First restored edit'; prompt.dispatchEvent(new Event('input', { bubbles: true }))
  await vi.advanceTimersByTimeAsync(500)
  prompt.value = 'Second restored edit'; prompt.dispatchEvent(new Event('input', { bubbles: true }))
  release({ ...project, revision: 2 })
  await flush()
  await vi.advanceTimersByTimeAsync(500)
  await flush()
  expect(api.updateVideoProject).toHaveBeenLastCalledWith(project.id, expect.objectContaining({ revision: 2 }), expect.any(AbortSignal))
})
it('pauses and releases its source player before the template ref disappears', async () => {
  await mount()
  const source = document.querySelector('audio')
  if (!source) throw new Error('Missing player')
  const pause = vi.spyOn(source, 'pause')
  app?.unmount(); app = undefined
  expect(pause).toHaveBeenCalled()
})
it('exposes an unavailable comparison model as disabled with setup information', async () => {
  await mount()
  button('2 Direction').click(); await flush()
  const option = document.querySelector<HTMLOptionElement>('option[value=ltx25]')
  expect(option?.disabled).toBe(true)
  expect(document.body.textContent).toContain('54.0 GiB')
})
it('does not send deleted checked shots in a selected-preview request', async () => {
  await mount()
  button('4 Preview').click(); await flush()
  const check = [...document.querySelectorAll<HTMLInputElement>('input[type=checkbox]')].find((element) => element.value === 'c'.repeat(32))
  if (!check) throw new Error('Missing shot checkbox')
  check.click(); await flush()
  button('3 Storyboard').click(); await flush()
  button('Shot 2').click(); await flush()
  button('Remove').click(); await flush()
  await vi.advanceTimersByTimeAsync(700)
  button('4 Preview').click(); await flush()
  button('Generate selected previews').click(); await flush()
  expect(api.previewVideoProject).toHaveBeenCalledWith(project.id, expect.objectContaining({ shot_ids: ['b'.repeat(32)] }), expect.any(AbortSignal))
})
it('requires FFmpeg before generating a preview even when the model is installed', async () => {
  vi.mocked(api.videoReadiness).mockResolvedValue({ ...videoReadinessFixture, ffmpeg_ready: false })
  await mount()
  button('3 Storyboard').click(); await flush()
  expect(button('Preview this shot').disabled).toBe(true)
  button('Preview this shot').click(); await flush()
  expect(api.previewVideoProject).not.toHaveBeenCalled()
})
it('keeps missing text support from breaking a full render while allowing a plain export', async () => {
  project.overlays = [{ id: 'f'.repeat(32), text: 'Timed title', start_sec: 0, end_sec: 4 }]
  vi.mocked(api.videoReadiness).mockResolvedValue({ ...videoReadinessFixture, overlay_ready: false })
  await mount()
  button('5 Export').click(); await flush()
  expect(button('Render missing shots and assemble').disabled).toBe(true)
  const includeText = [...document.querySelectorAll('label')].find((label) => label.textContent?.includes('Include timed titles and lyrics'))?.querySelector('input')
  if (!includeText) throw new Error('Missing timed text control')
  includeText.click(); await flush()
  expect(button('Render missing shots and assemble').disabled).toBe(false)
})
it('disables ignored generation options and reference influence in an image-based mode', async () => {
  project.mode = 'cover'
  await mount()
  button('2 Direction').click(); await flush()
  for (const name of ['Shared visual direction', 'Generation model', 'Denoise steps', 'Refine steps', 'Avoid in generated scenes']) {
    const input = [...document.querySelectorAll('label')].find((label) => label.textContent?.trim().startsWith(name))?.querySelector('input,select,textarea')
    expect(input, name).not.toBeNull()
    expect(input?.matches(':disabled'), name).toBe(true)
  }
  button('3 Storyboard').click(); await flush()
  const influence = [...document.querySelectorAll('label')].find((label) => label.textContent?.includes('Reference influence'))?.querySelector('input')
  expect(influence?.disabled).toBe(true)
  expect(document.body.textContent).toContain('first uploaded image')
})
it('disables audio analysis and shows its missing dependency guidance', async () => {
  vi.mocked(api.videoReadiness).mockResolvedValue({ ...videoReadinessFixture, analysis_ready: false })
  await mount()
  button('2 Direction').click(); await flush()
  expect(button('Analyze song').disabled).toBe(true)
  expect(document.body.textContent).toContain('NumPy')
  expect(document.body.textContent).toContain('backend dependencies')
  button('3 Storyboard').click(); await flush()
  expect(button('Re-analyze unlocked shots').disabled).toBe(true)
})
it('gates project creation and image upload when FFmpeg tools are missing', async () => {
  vi.mocked(api.videoReadiness).mockResolvedValue({ ...videoReadinessFixture, ffmpeg_ready: false })
  await mount()
  button('1 Song').click(); await flush()
  expect(button('Create a new project').disabled).toBe(true)
  button('2 Direction').click(); await flush()
  expect(document.querySelector<HTMLInputElement>('input[type=file]')?.disabled).toBe(true)
})
it('allows approved export during other model work while generation stays blocked', async () => {
  project.shots = project.shots?.map((shot, index) => ({ ...shot, approved_variant_id: String(index + 1).repeat(32) }))
  vi.mocked(api.otherWorkBusy).mockResolvedValue(true)
  vi.mocked(api.exportVideoProject).mockResolvedValue(project)
  await mount()
  button('5 Export').click(); await flush()
  expect(button('Render missing shots and assemble').disabled).toBe(true)
  expect(button('Export approved clips').disabled).toBe(false)
  button('Export approved clips').click(); await flush()
  expect(api.exportVideoProject).toHaveBeenCalledWith(project.id, { revision: 1, settings: project.export_settings }, expect.any(AbortSignal))
})
