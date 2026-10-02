import { SoundTouch } from '@soundtouchjs/core'

/**
 * Time-stretches an AudioBuffer by `tempoFactor` using Web Audio resampling + SoundTouch pitch shifting.
 *
 * SoundTouch's FIFO buffers are hard-wired to two samples per frame (there is no
 * channel-count setting in @soundtouchjs/core), so the pitch-shift stage always
 * runs in stereo. Mono input is duplicated into both channels and the result is
 * folded back to one channel; buffers with more than two channels keep their
 * first two, which is what the mixer graph plays anyway.
 */
export async function timeStretchBuffer(
  buffer: AudioBuffer,
  tempoFactor: number
): Promise<AudioBuffer> {
  if (tempoFactor === 1.0) return buffer

  const inChannels = buffer.numberOfChannels
  const outChannels = Math.min(inChannels, 2)
  const sampleRate = buffer.sampleRate
  const originalLength = buffer.length

  // 1. Resample using OfflineAudioContext (changes speed AND pitch)
  const stretchedLength = Math.ceil(originalLength / tempoFactor)
  const offlineCtx = new OfflineAudioContext(outChannels, stretchedLength, sampleRate)

  const source = offlineCtx.createBufferSource()
  source.buffer = buffer
  source.playbackRate.value = tempoFactor
  source.connect(offlineCtx.destination)
  source.start(0)

  const resampledBuffer = await offlineCtx.startRendering()

  // 2. Pitch shift back using SoundTouch (always 2 interleaved channels)
  const ST_CHANNELS = 2
  const st = new SoundTouch({ sampleRate, sampleBufferType: 'fifo' })
  st.pitch = 1 / tempoFactor

  // Interleave into stereo. For mono, the single channel feeds both slots.
  const left = resampledBuffer.getChannelData(0)
  const right = outChannels > 1 ? resampledBuffer.getChannelData(1) : left
  const interleaved = new Float32Array(stretchedLength * ST_CHANNELS)
  for (let i = 0; i < stretchedLength; i++) {
    interleaved[i * ST_CHANNELS] = left[i]
    interleaved[i * ST_CHANNELS + 1] = right[i]
  }

  // Process in chunks to prevent browser freeze on long tracks
  const chunkSize = 16384
  for (let i = 0; i < stretchedLength; i += chunkSize) {
    const chunkFrames = Math.min(chunkSize, stretchedLength - i)
    st.inputBuffer.putSamples(interleaved.subarray(i * ST_CHANNELS, (i + chunkFrames) * ST_CHANNELS))
    st.process()
    // Yield to the event loop every 16k frames (roughly 300ms of audio)
    await new Promise(r => setTimeout(r, 0))
  }

  // Push zero padding to flush the internal buffers (latency compensation)
  // SoundTouch WSOLA usually has latency around 2000-4000 samples.
  const latencyFrames = 8192
  st.inputBuffer.putSamples(new Float32Array(latencyFrames * ST_CHANNELS))
  st.process()

  const outFramesCount = st.outputBuffer.frameCount
  const finalInterleaved = new Float32Array(outFramesCount * ST_CHANNELS)
  // Use the common SampleBuffer contract: extract copies, receive consumes.
  // FifoSampleBuffer.receiveSamples performs these same steps, but is absent
  // from the common interface returned by SoundTouch.outputBuffer.
  st.outputBuffer.extract(finalInterleaved, 0, outFramesCount)
  st.outputBuffer.receive(outFramesCount)

  // We only take the exact stretchedLength frames
  const outLength = Math.max(1, Math.min(stretchedLength, outFramesCount))
  const factoryCtx = new OfflineAudioContext(1, 1, sampleRate)
  const outBuffer = factoryCtx.createBuffer(outChannels, outLength, sampleRate)

  for (let c = 0; c < outChannels; c++) {
    const channelData = outBuffer.getChannelData(c)
    for (let i = 0; i < outLength; i++) {
      channelData[i] = finalInterleaved[i * ST_CHANNELS + c]
    }
  }

  return outBuffer
}
