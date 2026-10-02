// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, type App } from 'vue'
import YueGenerationStatus from './YueGenerationStatus.vue'
import { i18n, setLocale } from '../../i18n'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('shows elapsed time and an honest unavailable state on stock engines', () => {
  setLocale('en'); const container = document.body.appendChild(document.createElement('div')); app = createApp(YueGenerationStatus, { stage: 'generating', elapsedSeconds: 15, nativeProgressAvailable: false }).use(i18n); app.mount(container)
  expect(container.textContent).toContain('Elapsed 15s'); expect(container.textContent).toContain('Native phase counts are unavailable'); expect(container.textContent).toContain('ETA unavailable'); expect(container.querySelector('[role="progressbar"]')).toBeNull()
})
it('reports progress only with a known denominator and labels ETA by phase', () => {
  setLocale('en'); const container = document.body.appendChild(document.createElement('div')); app = createApp(YueGenerationStatus, { nativeProgressAvailable: true, phaseEtaSeconds: 12, progress: { run_id: 'a'.repeat(32), phase: 'acoustic', current: 2, total: 8, started_ms: 1, phase_started_ms: 1, updated_ms: 2 } }).use(i18n); app.mount(container)
  expect(container.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('25'); expect(container.textContent).toContain('Estimated remaining in this phase: 12s')
})
