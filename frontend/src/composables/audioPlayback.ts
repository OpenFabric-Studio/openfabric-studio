/**
 * True module-level singletons shared by every WaveformPlayer instance.
 *
 * Declaring this state at the top of a <script setup> block (as it
 * originally was) does NOT give module-level sharing: <script setup>
 * compiles into the body of each component instance's own setup()
 * function, so a `let` there is re-created per instance - it just looks
 * like ordinary top-level code. A plain .ts module's top level, by
 * contrast, only runs once per module URL, so state declared here really
 * is one shared instance across every WaveformPlayer on the page.
 */

export const peaksCache = new Map<string, number[]>()
const inFlightPeaks = new Map<string, Promise<number[]>>()

const MAX_CONCURRENT_DECODES = 2
let activeDecodes = 0
const decodeQueue: Array<() => Promise<void>> = []

function processQueue(): void {
  while (activeDecodes < MAX_CONCURRENT_DECODES && decodeQueue.length > 0) {
    const task = decodeQueue.shift()!
    activeDecodes++
    task().finally(() => {
      activeDecodes--
      processQueue()
    })
  }
}

export function queueDecode<T>(fn: () => Promise<T>, priority = false): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const task = async () => {
      try {
        const result = await fn()
        resolve(result)
      } catch (e) {
        reject(e)
      }
    }
    if (priority) {
      decodeQueue.unshift(task)
    } else {
      decodeQueue.push(task)
    }
    processQueue()
  })
}

export async function fetchAndComputePeaks(src: string, barCount = 140, priority = false): Promise<number[]> {
  const cached = peaksCache.get(src)
  if (cached) return cached

  let inFlight = inFlightPeaks.get(src)
  if (inFlight) return inFlight

  inFlight = queueDecode(async () => {
    const resp = await fetch(src)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const buf = await resp.arrayBuffer()
    const audioBuffer = await getSharedAudioCtx().decodeAudioData(buf)
    const channel = audioBuffer.getChannelData(0)
    const blockSize = Math.max(1, Math.floor(channel.length / barCount))
    const result: number[] = []
    for (let i = 0; i < barCount; i++) {
      const start = i * blockSize
      let sum = 0
      for (let j = 0; j < blockSize && start + j < channel.length; j++) {
        sum += channel[start + j] * channel[start + j]
      }
      result.push(Math.sqrt(sum / blockSize))
    }
    const max = Math.max(...result, 1e-6)
    const normalized = result.map((v) => v / max)
    peaksCache.set(src, normalized)
    return normalized
  }, priority).finally(() => {
    inFlightPeaks.delete(src)
  })

  inFlightPeaks.set(src, inFlight)
  return inFlight
}

let sharedAudioCtx: AudioContext | null = null
export function getSharedAudioCtx(): AudioContext {
  if (!sharedAudioCtx) sharedAudioCtx = new AudioContext()
  return sharedAudioCtx
}

let currentlyPlaying: HTMLMediaElement | null = null

/** One active audio/video player; comparison previews must not overlap. */
export function claimPlayback(audio: HTMLMediaElement): void {
  if (currentlyPlaying && currentlyPlaying !== audio) currentlyPlaying.pause()
  currentlyPlaying = audio
}

export function releasePlaybackIfCurrent(audio: HTMLMediaElement): void {
  if (currentlyPlaying === audio) currentlyPlaying = null
}
