import { expect, it } from 'vitest'
import { trainingSampleKey } from './musicTrainingSamples'

it('keeps manifest revision and excerpt identity distinct from native dataset and track keys', () => {
  const source = { manifest_id: 'a'.repeat(32), manifest_sha256: 'b'.repeat(64), excerpt_id: 'c'.repeat(32) }
  const before = trainingSampleKey({ regional_manifest: source })
  expect(before).not.toBe('undefined')
  expect(trainingSampleKey({ regional_manifest: { ...source, manifest_sha256: 'd'.repeat(64) } })).not.toBe(before)
  expect(trainingSampleKey({ regional_manifest: { ...source, excerpt_id: 'e'.repeat(32) } })).not.toBe(before)
  expect(trainingSampleKey({ track_id: 42 })).toBe('42')
})
