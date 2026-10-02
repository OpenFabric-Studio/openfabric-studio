import { afterEach, expect, it, vi } from 'vitest'
import { base64AudioBlob } from './yue2'
afterEach(() => vi.restoreAllMocks())
it('decodes long audio in bounded slices without taking ownership of playback URLs', async () => {
  const source = Uint8Array.from({ length: 100003 }, (_value, index) => index % 256)
  const decode = vi.spyOn(globalThis, 'atob'); const create = vi.spyOn(URL, 'createObjectURL'); const revoke = vi.spyOn(URL, 'revokeObjectURL')
  const blob = base64AudioBlob(btoa(Array.from(source, byte => String.fromCharCode(byte)).join('')))
  expect(new Uint8Array(await blob.arrayBuffer())).toEqual(source); expect(blob.type).toBe('audio/wav')
  expect(decode.mock.calls.length).toBeGreaterThan(1); expect(decode.mock.calls.every(([slice]) => slice.length <= 32768)).toBe(true)
  expect(create).not.toHaveBeenCalled(); expect(revoke).not.toHaveBeenCalled()
})
it('preserves whitespace and unpadded decoding compatibility and rejects interior padding', async () => {
  expect(await base64AudioBlob('Y W\nJ\tj').text()).toBe('abc'); expect(await base64AudioBlob('YWI').text()).toBe('ab')
  expect(() => base64AudioBlob('YQ==YQ==')).toThrow(); expect(() => base64AudioBlob('!')).toThrow()
})
