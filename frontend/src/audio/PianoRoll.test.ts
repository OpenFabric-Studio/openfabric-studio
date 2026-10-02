// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import PianoRoll from '../components/editor/PianoRoll.vue'
import type { Clip } from './timelineTypes'

let unmount = (): void => {}

beforeEach(() => { document.body.innerHTML = '' })
afterEach(() => { unmount() })

it('emits only supported instrument values from the select boundary', async () => {
  const clip: Clip = { id: 'midi', type: 'midi', sourceLabel: 'MIDI', timelineStart: 0, trimStart: 0, trimEnd: 4, notes: [] }
  const emitted: unknown[] = []
  const mount = document.createElement('div')
  document.body.append(mount)
  const app = createApp(PianoRoll, { clip, onUpdateInstrument: (value: unknown) => emitted.push(value) })
  app.use(createPinia())
  app.mount(mount)
  unmount = () => app.unmount()
  const select = mount.querySelector('select')
  if (!(select instanceof HTMLSelectElement)) throw new Error('Instrument selector missing')

  select.value = 'triangle'
  select.dispatchEvent(new Event('change'))
  await nextTick()
  const unsupported = document.createElement('option')
  unsupported.value = 'custom'
  select.append(unsupported)
  select.value = 'custom'
  select.dispatchEvent(new Event('change'))
  await nextTick()

  expect(emitted).toEqual(['triangle'])
})
