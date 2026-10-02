// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useEditorStore } from './editor'

beforeEach(() => { setActivePinia(createPinia()) })

it('restores a validated undo snapshot while retaining the current zoom', () => {
  const store = useEditorStore()
  store.snapshot()
  store.addLane()
  store.setZoom(90)
  store.undo()
  expect(store.project.lanes).toHaveLength(2)
  expect(store.project.pxPerSecond).toBe(90)
  expect(store.project.master).toBeDefined()
})

it('keeps the current project when an undo snapshot is corrupt', () => {
  const store = useEditorStore()
  const original = store.project
  store.history = ['{"lanes":"invalid"}']
  expect(() => store.restoreHistory(0)).not.toThrow()
  expect(store.project).toBe(original)
  expect(store.error).toBeTruthy()
})

it('creates the first loop from the project duration instead of a disabled ten-second placeholder', () => {
  const store = useEditorStore()
  expect(store.project.loopRegion).toBeUndefined()
  store.project.lanes[0]?.clips.push({ id: 'clip', sourceLabel: 'Audio', timelineStart: 5, trimStart: 0, trimEnd: 12 })
  store.toggleLoop()
  expect(store.project.loopRegion).toEqual({ start: 0, end: 17, enabled: true })
})

it('uses ten seconds for an empty project and preserves saved loop bounds when toggling', () => {
  const store = useEditorStore()
  store.toggleLoop()
  expect(store.project.loopRegion).toEqual({ start: 0, end: 10, enabled: true })
  store.setLoopRegion(3, 14)
  store.toggleLoop()
  store.toggleLoop()
  expect(store.project.loopRegion).toEqual({ start: 3, end: 14, enabled: true })
})

it('normalizes loop bounds using the clamped start and ignores non-finite input', () => {
  const store = useEditorStore()
  store.setLoopRegion(-2, -1)
  expect(store.project.loopRegion).toEqual({ start: 0, end: 0.1, enabled: true })
  store.setLoopRegion(Number.NaN, 10)
  store.setLoopRegion(0, Number.POSITIVE_INFINITY)
  expect(store.project.loopRegion).toEqual({ start: 0, end: 0.1, enabled: true })
  store.setLoopRegion(Number.MAX_VALUE, Number.MAX_VALUE)
  expect(store.project.loopRegion).toEqual({ start: 0, end: 0.1, enabled: true })
})
