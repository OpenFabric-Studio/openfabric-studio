// @vitest-environment happy-dom
import { afterEach,expect,it } from 'vitest'
import { createApp,type App } from 'vue'
import { i18n } from '../../i18n'
import AudioQualityReport from './AudioQualityReport.vue'
let app: App | undefined
 afterEach(() => { app?.unmount();document.body.replaceChildren() })
it.each(['warning','inconclusive'] as const)('shows the captured %s target assessment even when encoded metrics are unavailable', targetResult => {
  app=createApp(AudioQualityReport,{metrics:null,targetResult}).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')))
  expect(document.querySelector('[data-audio-quality]')?.textContent).toContain(i18n.global.t(`exportQuality.targetResult.${targetResult}`))
})
