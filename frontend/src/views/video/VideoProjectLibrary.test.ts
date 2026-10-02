// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import VideoProjectLibrary from './VideoProjectLibrary.vue'
import type { VideoProject, VideoProjectJob } from '../../api/contracts'
import { i18n, setLocale } from '../../i18n'
import { videoProjectFixture } from './videoFixtures'

let app: App | undefined
let rows = ref<VideoProject[]>([])
let busy = ref(false)
const opened: string[] = [], removed: string[] = []
let beginDelete = (): void => {}
const confirm = vi.fn<(message?: string) => boolean>(() => false)
function project(index: number, status: VideoProjectJob['status'] | 'draft' = 'draft'): VideoProject {
  const row = { ...videoProjectFixture(index.toString(16).padStart(32, '0')), name: `Project ${index}` }
  if (status !== 'draft') row.job = { id: 'f'.repeat(32), operation: 'preview', status }
  return row
}
beforeEach(() => { setLocale('en'); busy = ref(false); opened.length = 0; removed.length = 0; beginDelete = () => {}; confirm.mockReset().mockReturnValue(false); vi.stubGlobal('confirm', confirm) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals(); setLocale('en') })
async function settle() { for (let i = 0; i < 4; i++) await nextTick() }
async function mount(projects: VideoProject[], selectedId: string | null = null) {
  rows = ref(projects)
  app = createApp({ render: () => h(VideoProjectLibrary, { projects: rows.value, selectedId, busy: busy.value, onOpen: (id: string) => opened.push(id), onDelete: (id: string) => { removed.push(id); beginDelete() } }) }).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div'))); await settle()
}
function row(id: string): HTMLElement {
  const target = document.querySelector<HTMLElement>(`[data-video-project="${id}"]`)
  if (!target) throw new Error(`Missing project ${id}`)
  return target
}
function remove(id: string): HTMLButtonElement {
  const button = row(id).querySelector<HTMLButtonElement>('[data-delete-project]')
  if (!button) throw new Error('Missing delete action')
  return button
}
function next() {
  const button = document.querySelector<HTMLButtonElement>('[data-pagination-next]')
  if (!button) throw new Error('Missing next page')
  button.click()
}
function open(id: string): HTMLButtonElement {
  const button = row(id).querySelector<HTMLButtonElement>('[data-open-project]')
  if (!button) throw new Error('Missing open action')
  return button
}
async function startFocusedDeletion(id: string) {
  const initiating = remove(id)
  beginDelete = () => { busy.value = true }
  initiating.focus(); confirm.mockReturnValue(true); initiating.click()
  await settle()
  return initiating
}

it('names the exact project and removal scope in confirmation, then emits only after confirmation', async () => {
  const first = project(1), second = project(2)
  await mount([first, second], first.id)
  remove(second.id).click()
  const message = confirm.mock.calls[0]?.[0]
  expect(message).toContain('Project 2')
  for (const scope of ['permanently', 'project files', 'references', 'previews', 'renders', 'source song is kept']) expect(message).toContain(scope)
  expect(removed).toEqual([])
  confirm.mockReturnValue(true); remove(second.id).click()
  expect(removed).toEqual([second.id])
  expect(row(first.id).getAttribute('aria-current')).toBe('true')
  expect(row(first.id).textContent).toContain('Selected project')
})

it.each(['queued', 'running'] as const)('requires cancellation before deleting a %s project', async status => {
  const active = project(1, status)
  await mount([active]); confirm.mockReturnValue(true)
  expect(remove(active.id).disabled).toBe(true)
  expect(row(active.id).textContent).toContain('Open this project and cancel its active job before deleting it')
  remove(active.id).click()
  expect(confirm).not.toHaveBeenCalled(); expect(removed).toEqual([])
  row(active.id).querySelector<HTMLButtonElement>('[data-open-project]')?.click()
  expect(opened).toEqual([active.id])
})

it('disables project actions while the workspace is acting or saving', async () => {
  const saved = { ...project(1), file_url: '/video.mp4' }
  await mount([saved]); busy.value = true; await settle()
  const open = row(saved.id).querySelector<HTMLButtonElement>('[data-open-project]')
  expect(open?.disabled).toBe(true); expect(remove(saved.id).disabled).toBe(true)
  expect(row(saved.id).querySelector('a')?.getAttribute('href')).toBeNull()
  open?.click(); remove(saved.id).click()
  expect(opened).toEqual([]); expect(removed).toEqual([]); expect(confirm).not.toHaveBeenCalled()
})

it('shows ten projects per page and clamps when the final page is removed', async () => {
  await mount(Array.from({ length: 21 }, (_, index) => project(index + 1)))
  expect(document.querySelectorAll('[data-video-project]')).toHaveLength(10)
  next(); await settle(); next(); await settle()
  expect(document.querySelectorAll('[data-video-project]')).toHaveLength(1)
  expect(row(project(21).id)).not.toBeNull()
  rows.value = rows.value.filter(item => item.id !== project(21).id); await settle()
  expect(document.querySelectorAll('[data-video-project]')).toHaveLength(10)
  expect(document.querySelector('[aria-current=page]')?.textContent).toBe('2')
  rows.value = []; await settle()
  expect(document.body.textContent).toContain('No saved video projects yet')
  expect(document.querySelector('[data-pagination]')).toBeNull()
})

it('resets pagination for search and status, and shows an empty result message', async () => {
  const projects = Array.from({ length: 21 }, (_, index) => project(index + 1))
  projects[0] = { ...project(1, 'ready'), track_title: 'Ready song' }
  await mount(projects); next(); await settle()
  const search = document.querySelector<HTMLInputElement>('input[type=search]')
  const status = document.querySelector<HTMLSelectElement>('[data-project-status]')
  if (!search || !status) throw new Error('Missing library filters')
  search.value = ' READY SONG '; search.dispatchEvent(new Event('input')); await settle()
  expect(document.querySelectorAll('[data-video-project]')).toHaveLength(1)
  expect(row(project(1).id)).not.toBeNull()
  search.value = ''; search.dispatchEvent(new Event('input')); await settle()
  next(); await settle()
  status.value = 'ready'; status.dispatchEvent(new Event('change')); await settle()
  expect(document.querySelectorAll('[data-video-project]')).toHaveLength(1)
  status.value = 'failed'; status.dispatchEvent(new Event('change')); await settle()
  expect(document.querySelectorAll('[data-video-project]')).toHaveLength(0)
  expect(document.body.textContent).toContain('No projects match your search or status filter')
})

it('translates project deletion and its scope in Russian', async () => {
  setLocale('ru'); const saved = project(1)
  await mount([saved]); remove(saved.id).click()
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Исходная песня сохраняется'))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining(saved.name))
  expect(document.body.textContent).not.toContain('videoWorkspace.')
})

it.each([{ size: 3, remove: 2, next: 3 }, { size: 10, remove: 10, next: 9 }])('restores keyboard deletion focus from project $remove to nearby project $next', async scenario => {
  await mount(Array.from({ length: scenario.size }, (_, index) => project(index + 1)))
  await startFocusedDeletion(project(scenario.remove).id)
  rows.value = rows.value.filter(item => item.id !== project(scenario.remove).id); busy.value = false; await settle()
  expect(document.activeElement).toBe(open(project(scenario.next).id))
})

it('restores focus to the preceding row when deleting the last project on a clamped page', async () => {
  await mount(Array.from({ length: 21 }, (_, index) => project(index + 1)))
  next(); await settle(); next(); await settle()
  await startFocusedDeletion(project(21).id)
  rows.value = rows.value.filter(item => item.id !== project(21).id); busy.value = false; await settle()
  expect(document.querySelector('[aria-current=page]')?.textContent).toBe('2')
  expect(document.activeElement).toBe(open(project(20).id))
})

it('focuses the library heading when keyboard deletion removes the last project', async () => {
  await mount([project(1)])
  await startFocusedDeletion(project(1).id)
  rows.value = []; busy.value = false; await settle()
  const heading = document.querySelector('#video-project-library-title')
  expect(heading?.getAttribute('tabindex')).toBe('-1')
  expect(document.activeElement).toBe(heading)
})

it.each(['search', 'outside'] as const)('keeps user focus on another %s control during pending deletion', async destination => {
  await mount([project(1), project(2)])
  await startFocusedDeletion(project(1).id)
  const target = destination === 'search' ? document.querySelector<HTMLInputElement>('input[type=search]')
    : document.body.appendChild(document.createElement('button'))
  if (!target) throw new Error('Missing next focus control')
  target.focus()
  rows.value = rows.value.filter(item => item.id !== project(1).id); busy.value = false; await settle()
  expect(document.activeElement).toBe(target)
})

it('restores the initiating action after failed deletion drops disabled-button focus', async () => {
  await mount([project(1), project(2)])
  const initiating = await startFocusedDeletion(project(1).id)
  // Browsers can blur an action when the workspace disables it during a request.
  // Happy DOM ignores blur() on disabled buttons; simulate the browser's drop.
  initiating.disabled = false; initiating.blur(); initiating.disabled = true
  expect(document.activeElement).toBe(document.body)
  busy.value = false; await settle()
  expect(initiating.disabled).toBe(false)
  expect(document.activeElement).toBe(initiating)
})

it('leaves moved user focus alone when deletion fails', async () => {
  await mount([project(1), project(2)])
  await startFocusedDeletion(project(1).id)
  const search = document.querySelector<HTMLInputElement>('input[type=search]')
  if (!search) throw new Error('Missing search')
  search.focus(); busy.value = false; await settle()
  expect(document.activeElement).toBe(search)
})
