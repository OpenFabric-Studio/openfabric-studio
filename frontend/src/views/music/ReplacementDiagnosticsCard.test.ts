// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import { replacementDiagnosticsFixture } from './replacementDiagnosticsTestFixtures'
import ReplacementDiagnosticsCard from './ReplacementDiagnosticsCard.vue'

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })

async function mount(disabled = false) {
  const pitch = vi.fn(), eq = vi.fn(), node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(ReplacementDiagnosticsCard, { report: replacementDiagnosticsFixture(), disabled, onPitch: pitch, onEq: eq }) }).use(i18n)
  app.mount(node); await nextTick(); return { node, pitch, eq }
}
function button(node: HTMLElement, text: string) {
  const result = [...node.querySelectorAll('button')].find(item => item.textContent?.includes(text))
  if (!result) throw new Error(`Missing ${text}`)
  return result
}
it('shows measured support and uncertainty, and changes settings only after explicit review', async () => {
  const { node, pitch, eq } = await mount()
  expect(node.textContent).toContain('15.0 s sampled')
  expect(node.textContent).toContain('Confidence has not been calibrated')
  expect(node.textContent).toContain('Shared instruments can correlate')
  expect(node.textContent).toContain('inverted')
  expect(node.textContent).toContain('Several delays fit this window')
  expect(pitch).not.toHaveBeenCalled(); expect(eq).not.toHaveBeenCalled()
  button(node, 'Use proposed pitch').click(); button(node, 'Use proposed EQ').click()
  expect(pitch).toHaveBeenCalledWith(expect.objectContaining({ pitch_shift_semitones: 2, pitch_nudge_semitones: 0 }))
  expect(eq).toHaveBeenCalledWith([expect.objectContaining({ frequency_hz: 125, gain_db: 1 })])
})
it('blocks applying an old report while another revision or local draft is being reviewed', async () => {
  const { node, pitch, eq } = await mount(true)
  expect(button(node, 'Use proposed pitch').disabled).toBe(true)
  expect(button(node, 'Use proposed EQ').disabled).toBe(true)
  button(node, 'Use proposed pitch').click(); button(node, 'Use proposed EQ').click()
  expect(pitch).not.toHaveBeenCalled(); expect(eq).not.toHaveBeenCalled()
})
