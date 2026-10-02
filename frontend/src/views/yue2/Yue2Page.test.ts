// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, ref, type App } from 'vue'
import Yue2Page from './Yue2Page.vue'

const statuses = reactive({ yue2: { status: 'running', error: null } })
vi.mock('../../stores/orchestrator', () => ({ useOrchestratorStore: () => ({ statuses }) }))
vi.mock('../../stores/yue2', () => ({ useYue2Store: () => ({ loadHistory: vi.fn(), startBackgroundTasks: vi.fn(), stopBackgroundTasks: vi.fn() }) }))
vi.mock('../../components/shared/ModelOfflineBanner.vue', () => ({ default: defineComponent({ render: () => h('p', 'Engine offline') }) }))
vi.mock('./TrackFeed.vue', () => ({ default: defineComponent({ render: () => h('div') }) }))
vi.mock('./GenerateForm.vue', () => ({ default: defineComponent({ props: ['generationAvailable'], setup(props) { const draft = ref(''); return () => h('input', { value: draft.value, disabled: !props.generationAvailable, onInput: (event: Event) => { if (event.target instanceof HTMLInputElement) draft.value = event.target.value } }) } }) }))
let app: App | undefined
afterEach(() => { app?.unmount(); document.body.replaceChildren(); statuses.yue2.status = 'running' })
it('retains the generator draft while an engine is stopped and restarted', async () => {
  const container = document.body.appendChild(document.createElement('div')); app = createApp(Yue2Page); app.mount(container); await nextTick()
  const input = container.querySelector('input'); if (!input) throw new Error('Form missing')
  input.value = 'Unfinished lyrics'; input.dispatchEvent(new Event('input')); await nextTick()
  statuses.yue2.status = 'stopped'; await nextTick()
  expect(container.querySelector('input')).toBe(input); expect(input.disabled).toBe(true)
  statuses.yue2.status = 'running'; await nextTick()
  expect(input.value).toBe('Unfinished lyrics'); expect(input.disabled).toBe(false)
})
