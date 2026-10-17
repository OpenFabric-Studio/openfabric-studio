// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import { parseVoiceMixReport } from '../../api/generated'
import { measuredVoiceMixFixture } from './voiceMixTestFixture'
import VoiceMixReport from './VoiceMixReport.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('presents retained measured diagnostics and all five comparison sources', async () => {
  const report = parseVoiceMixReport(measuredVoiceMixFixture)
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp(VoiceMixReport, { report }).use(i18n); app.mount(node); await nextTick()
  expect(node.textContent).toContain('Applied vocal gain: 6.00 dB')
  expect(node.querySelectorAll('audio')).toHaveLength(5)
  expect(node.querySelector('[data-audio-quality]')?.textContent).toContain('LUFS')
  expect(node.textContent).toContain('fixture')
})
it('describes missing completed diagnostics without claiming a measured result', async () => {
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp(VoiceMixReport, { requested: true, completed: true }).use(i18n); app.mount(node); await nextTick()
  expect(node.textContent).toContain('report is unavailable'); expect(node.querySelector('audio')).toBeNull()
})
