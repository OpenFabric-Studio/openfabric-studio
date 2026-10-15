// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import StemsPanel from './StemsPanel.vue'
import * as stemsApi from '../../api/stems'
import * as projectsApi from '../../api/projects'
import { decodeStem } from '../../audio/mixerEngine'
import { TestAudioBuffer } from '../../audio/testWebAudio'
import { i18n } from '../../i18n'
import type { ProjectFull } from '../../api/projects'

vi.mock('../../api/stems', () => ({ getSeparationStatus: vi.fn(), startSeparation: vi.fn(), cancelSeparation: vi.fn(), deleteStems: vi.fn() }))
vi.mock('../../api/projects', async original => ({ ...await original<typeof import('../../api/projects')>(), createProject: vi.fn() }))
vi.mock('../../audio/mixerEngine', async original => ({ ...await original<typeof import('../../audio/mixerEngine')>(), decodeStem: vi.fn() }))
vi.mock('./WaveformPlayer.vue', () => ({ default: { render: () => null } }))
vi.mock('./StemExportControls.vue', () => ({ default: { render: () => null } }))
let app: App | undefined
async function settle() { for (let index = 0; index < 12; index++) await nextTick() }
function deferred<T>() { let resolve: (value: T) => void = () => { throw new Error('Not initialized') }; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(false))
  vi.mocked(stemsApi.getSeparationStatus).mockResolvedValue({ status: 'done', error: null, stems: { vocals: '/stem.wav' } })
  vi.mocked(stemsApi.startSeparation).mockResolvedValue({ status: 'queued', error: null, stems: null })
  vi.mocked(projectsApi.createProject).mockImplementation(async (name, data) => ({ id: 51, name, data, created_at: 'now', updated_at: 'now' }))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })
async function mount(requireEditor = true) {
  const shown = ref(true), trackId = ref(1)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }, { path: '/other', component: { render: () => null } }, { path: '/editor/:id', component: { render: () => null } }] })
  app = createApp({ render: () => shown.value ? h(StemsPanel, { trackId: trackId.value, title: 'Song', lyrics: '', model: 'ace_step' }) : h('div') }).use(i18n).use(router)
  const root = document.body.appendChild(document.createElement('div')); app.mount(root); await settle()
  root.querySelector('button')?.click(); await settle()
  const open = [...root.querySelectorAll('button')].find(button => button.textContent?.trim() === i18n.global.t('mixer.openInEditor'))
  const control = requireEditor ? open : root.querySelector('button')
  if (!control) throw new Error('Missing Open in editor')
  return { shown, trackId, router, open: control }
}

it.each([false, true])('warns before replacing old stem files and recreates only when accepted: %s', async accepted => {
  await mount()
  vi.mocked(confirm).mockReturnValue(accepted)
  const recreate = [...document.querySelectorAll('button')].find(button => button.textContent?.trim() === 'Recreate')
  if (!recreate) throw new Error('Missing Recreate')
  recreate.click(); await settle()
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/replace.*stem.*files.*removed/i))
  if (accepted) expect(stemsApi.startSeparation).toHaveBeenCalledExactlyOnceWith(1, true)
  else {
    expect(stemsApi.startSeparation).not.toHaveBeenCalled()
    expect(stemsApi.cancelSeparation).not.toHaveBeenCalled()
    expect(stemsApi.deleteStems).not.toHaveBeenCalled()
  }
})

it('starts initial stem separation without a removal confirmation', async () => {
  vi.mocked(stemsApi.getSeparationStatus).mockResolvedValue({ status: 'idle', error: null, stems: null })
  await mount(false)
  const split = [...document.querySelectorAll('button')].find(button => button.textContent?.trim() === 'Split into stems')
  if (!split) throw new Error('Missing Split into stems')
  split.click(); await settle()
  expect(confirm).not.toHaveBeenCalled()
  expect(stemsApi.startSeparation).toHaveBeenCalledExactlyOnceWith(1, false)
})

it.each(['unmount', 'track-change'] as const)('does not create an editor project after stem decoding is superseded by %s', async change => {
  const decoding = deferred<AudioBuffer>(); vi.mocked(decodeStem).mockReturnValue(decoding.promise)
  const { shown, trackId, router, open } = await mount()
  open.click(); await settle()
  expect(decodeStem).toHaveBeenCalledOnce()
  if (change === 'unmount') shown.value = false
  else trackId.value = 2
  await settle(); await router.push('/other')
  decoding.resolve(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 })); await settle()
  expect(projectsApi.createProject).not.toHaveBeenCalled()
  expect(router.currentRoute.value.path).toBe('/other')
})

it('retains an accepted editor project without navigating after its stem card unmounts', async () => {
  vi.mocked(decodeStem).mockResolvedValue(new AudioBuffer({ numberOfChannels: 1, length: 8000, sampleRate: 8000 }))
  const creating = deferred<ProjectFull>(); vi.mocked(projectsApi.createProject).mockReturnValue(creating.promise)
  const { shown, router, open } = await mount(), navigation = vi.spyOn(router, 'push')
  open.click(); await settle()
  const body = vi.mocked(projectsApi.createProject).mock.calls[0]?.[1]
  if (!body) throw new Error('Missing project request')
  shown.value = false; await settle(); await router.push('/other'); navigation.mockClear()
  creating.resolve({ id: 51, name: 'Song', data: body, created_at: 'now', updated_at: 'now' }); await settle()
  expect(projectsApi.createProject).toHaveBeenCalledOnce()
  expect(navigation).not.toHaveBeenCalled()
  expect(router.currentRoute.value.path).toBe('/other')
})
