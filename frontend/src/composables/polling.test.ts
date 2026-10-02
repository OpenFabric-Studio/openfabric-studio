import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPollingLoop } from './polling'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

it('ignores old responses after a stopped loop starts again', async () => {
  const releases: Array<() => void> = []
  let applied = 0
  const loop = createPollingLoop(async (context) => {
    await new Promise<void>((resolve) => { releases.push(resolve) })
    if (context.isCurrent()) applied++
  }, 100)
  loop.start()
  loop.start()
  expect(releases).toHaveLength(1)
  loop.stop()
  loop.start()
  releases[0]?.()
  await vi.advanceTimersByTimeAsync(500)
  expect(applied).toBe(0)
  expect(releases).toHaveLength(2)
  releases[1]?.()
  await vi.advanceTimersByTimeAsync(100)
  expect(applied).toBe(1)
  expect(releases).toHaveLength(3)
  loop.stop()
  releases[2]?.()
})

it('aborts the current request and never overlaps slow polling requests', async () => {
  let currentSignal: AbortSignal | undefined
  let release: (() => void) | undefined
  let requests = 0
  const loop = createPollingLoop(async ({ signal }) => {
    currentSignal = signal
    requests++
    await new Promise<void>((resolve) => { release = resolve })
  }, 100)
  loop.start()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(requests).toBe(1)
  loop.stop()
  expect(currentSignal?.aborted).toBe(true)
  release?.()
  await vi.advanceTimersByTimeAsync(100)
  expect(requests).toBe(1)
})

it('stops when its work is complete and can start for new work', async () => {
  let requests = 0
  const loop = createPollingLoop(async () => { requests++; return false }, 100)
  loop.start()
  await vi.advanceTimersByTimeAsync(1000)
  expect(requests).toBe(1)
  loop.start()
  await vi.advanceTimersByTimeAsync(1000)
  expect(requests).toBe(2)
})
