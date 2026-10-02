// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import VoiceSelect from './VoiceSelect.vue'
import * as voices from '../../api/voices'
import * as ace from '../../api/aceStep'
import { useAceStepStore } from '../../stores/aceStep'
import { i18n, setLocale } from '../../i18n'
import { voiceProfile } from '../../views/voice/voiceTestFixtures'

vi.mock('../../api/voices', async (original) => ({ ...await original<typeof import('../../api/voices')>(), listVoices: vi.fn() }))
vi.mock('../../api/aceStep', () => ({ releaseTask: vi.fn() }))

let app: App | undefined
beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); vi.resetAllMocks(); localStorage.clear(); setLocale('en') })
afterEach(() => { app?.unmount(); app = undefined; useAceStepStore().stopBackgroundTasks(); document.body.replaceChildren(); vi.clearAllTimers(); vi.useRealTimers() })
async function settle() { for (let index = 0; index < 5; index++) await nextTick() }
async function mount(onSelect: (id: string | null) => void) {
  app = createApp(VoiceSelect, { onSelect })
  app.use(i18n).use(createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] }))
  const container = document.createElement('div'); document.body.append(container); app.mount(container); await settle(); return container
}

it('clears a validated existing unusable cached voice so ordinary ACE generation submits no voice', async () => {
  const voice = voiceProfile()
  voices.setActiveVoiceId(voice.id)
  vi.mocked(voices.listVoices).mockResolvedValue([voice])
  vi.mocked(ace.releaseTask).mockResolvedValue({ task_id: 'ordinary-job', status: 'queued', queue_position: 1 })
  const selected = vi.fn<(id: string | null) => void>()
  await mount(selected)
  await useAceStepStore().submit({ prompt: 'Folk' }, null, 'Ordinary generation')
  expect(ace.releaseTask).toHaveBeenCalledWith({ prompt: 'Folk' }, null, 'Ordinary generation', null, undefined, undefined)
  expect(voices.getActiveVoiceId()).toBeNull()
  expect(selected).toHaveBeenLastCalledWith(null)
})

it('preserves a cached voice when listing fails instead of treating a network error as unusable', async () => {
  const voice = voiceProfile()
  voices.setActiveVoiceId(voice.id)
  vi.mocked(voices.listVoices).mockRejectedValue(new Error('Offline'))
  const selected = vi.fn<(id: string | null) => void>()
  await mount(selected)
  await vi.advanceTimersByTimeAsync(5000); await settle()
  expect(voices.getActiveVoiceId()).toBe(voice.id)
  expect(selected).toHaveBeenLastCalledWith(null)
})
