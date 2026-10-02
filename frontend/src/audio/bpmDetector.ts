/**
 * Extremely simple and robust BPM detector for Web Audio API.
 * Uses an OfflineAudioContext to apply a low-pass filter, then detects peaks
 * and calculates the mode (most common) interval between peaks to estimate BPM.
 */

export async function detectBpm(buffer: AudioBuffer): Promise<number | null> {
  // Only process up to 30 seconds of audio to save computation time
  const processDuration = Math.min(buffer.duration, 30)
  const offlineCtx = new OfflineAudioContext(1, processDuration * buffer.sampleRate, buffer.sampleRate)

  const source = offlineCtx.createBufferSource()
  source.buffer = buffer

  // Low-pass filter to isolate kick/bass frequencies
  const filter = offlineCtx.createBiquadFilter()
  filter.type = 'lowpass'
  filter.frequency.value = 150
  filter.Q.value = 1

  source.connect(filter)
  filter.connect(offlineCtx.destination)
  source.start(0)

  const filteredBuffer = await offlineCtx.startRendering()
  const data = filteredBuffer.getChannelData(0)

  // Find max value for thresholding
  let maxVal = 0
  for (let i = 0; i < data.length; i++) {
    const val = Math.abs(data[i])
    if (val > maxVal) maxVal = val
  }

  const threshold = maxVal * 0.8
  const peaks: number[] = []
  
  // Find peaks with a minimum distance (e.g. 200ms)
  for (let i = 0; i < data.length; i++) {
    if (data[i] > threshold) {
      peaks.push(i)
      i += buffer.sampleRate * 0.2 // Skip 200ms to avoid double-counting the same beat
    }
  }

  if (peaks.length < 2) return null // Not enough data

  // Calculate intervals between all pairs of peaks
  const intervals: Record<number, number> = {}
  for (let i = 1; i < peaks.length; i++) {
    for (let j = 0; j < i; j++) {
      const diffSamples = peaks[i] - peaks[j]
      const diffSec = diffSamples / buffer.sampleRate
      if (diffSec === 0) continue
      
      let bpm = 60 / diffSec
      
      // Normalize BPM into a standard range (70-160)
      while (bpm < 70) bpm *= 2
      while (bpm > 160) bpm /= 2
      
      const roundedBpm = Math.round(bpm)
      intervals[roundedBpm] = (intervals[roundedBpm] || 0) + 1
    }
  }

  // Find the most frequent BPM (the mode)
  let bestBpm: number | null = null
  let maxCount = 0
  for (const [bpmStr, count] of Object.entries(intervals)) {
    if (count > maxCount) {
      maxCount = count
      bestBpm = parseInt(bpmStr, 10)
    }
  }

  return bestBpm
}
