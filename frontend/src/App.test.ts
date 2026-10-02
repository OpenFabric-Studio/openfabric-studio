// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App as VueApp } from 'vue'
import App from './App.vue'
import { i18n, setLocale } from './i18n'
import { completionNotifications, markGenerationsRead } from './composables/completionNotifications'
vi.mock('./stores/orchestrator', () => ({ useOrchestratorStore: () => ({ startPolling: vi.fn(), stopPolling: vi.fn() }) }))
vi.mock('./components/shared/AppShell.vue', () => ({ default: defineComponent({ render() { return h('div', this.$slots.default?.()) } }) }))
let app: VueApp | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); completionNotifications.unread = [] })
it('shows and clears the unread completion count in the document title', async () => {
  setLocale('en'); const container = document.body.appendChild(document.createElement('div')); app = createApp(App).use(i18n); app.component('RouterView', defineComponent({ render: () => null })); app.mount(container)
  completionNotifications.unread = ['ace:a', 'yue:b']; await nextTick(); expect(document.title).toMatch(/^\(2\) OpenFabric/)
  markGenerationsRead(); await nextTick(); expect(document.title).toMatch(/^OpenFabric/)
})
