export interface PollContext {
  signal: AbortSignal
  isCurrent: () => boolean
}

export interface PollingLoop {
  start: (immediate?: boolean) => void
  stop: () => void
}

/** One owned request at a time. Stopping invalidates responses even when fetch ignores abort. */
export function createPollingLoop(
  task: (context: PollContext) => Promise<boolean | void>,
  delay: number | (() => number),
): PollingLoop {
  let generation = 0
  let active = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  const waitMs = () => typeof delay === 'number' ? delay : delay()
  function stop() {
    active = false
    generation++
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
    controller?.abort()
    controller = undefined
  }
  function start(immediate = true) {
    if (active) return
    active = true
    const token = ++generation
    const requestController = new AbortController()
    controller = requestController
    const isCurrent = () => active && token === generation
    const tick = async () => {
      timer = undefined
      let keepGoing: boolean | void = undefined
      try {
        keepGoing = await task({ signal: requestController.signal, isCurrent })
      } catch {
        // Callers own visible errors; an unexpected rejection cannot leak the loop.
      }
      if (!isCurrent()) return
      if (keepGoing === false) {
        stop()
        return
      }
      timer = setTimeout(() => { void tick() }, waitMs())
    }
    if (immediate) void tick()
    else timer = setTimeout(() => { void tick() }, waitMs())
  }
  return { start, stop }
}
