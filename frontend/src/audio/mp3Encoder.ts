import { Mp3Encoder } from '@breezystack/lamejs'

function floatTo16BitPCM(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]))
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return output
}

/** Encodes an AudioBuffer to an MP3 Blob via lamejs, frame-by-frame. */
export function encodeMp3(buffer: AudioBuffer, kbps = 192): Blob {
  const channels = Math.min(2, buffer.numberOfChannels)
  const left = floatTo16BitPCM(buffer.getChannelData(0))
  const right = channels > 1 ? floatTo16BitPCM(buffer.getChannelData(1)) : undefined

  const encoder = new Mp3Encoder(channels, buffer.sampleRate, kbps)
  const chunks: Uint8Array[] = []
  const frameSize = 1152

  for (let i = 0; i < left.length; i += frameSize) {
    const leftChunk = left.subarray(i, i + frameSize)
    const rightChunk = right ? right.subarray(i, i + frameSize) : undefined
    const mp3buf = encoder.encodeBuffer(leftChunk, rightChunk)
    if (mp3buf.length > 0) chunks.push(mp3buf)
  }
  const end = encoder.flush()
  if (end.length > 0) chunks.push(end)

  return new Blob(chunks.map((c) => new Uint8Array(c)), { type: 'audio/mp3' })
}
