import { expect, it } from 'vitest'
import { workflowAudioUrl } from './audiobookWorkflow'
const path = `/api/audiobooks/${'a'.repeat(32)}/passages/${'b'.repeat(32)}/audio`
it('accepts the backend revision-qualified passage URL without duplicating the query', () => {
  expect(workflowAudioUrl(`${path}?revision=3`, 3)).toBe(`${path}?revision=3`)
  expect(workflowAudioUrl(`${path}?revision=3`)).toBe(`${path}?revision=3`)
  expect(workflowAudioUrl(path, 4)).toBe(`${path}?revision=4`)
})
it.each(['https://outside.invalid/audio', '//outside.invalid/audio', `${path}?revision=0`, `${path}?revision=3&redirect=elsewhere`, `${path}?revision=9999999999999999999999999`, `${path}/../audio`])('rejects unsafe or invalid media URLs %s', value => {
  expect(workflowAudioUrl(value)).toBeUndefined()
})
it('rejects stale URL metadata instead of playing the wrong accepted revision', () => {
  expect(workflowAudioUrl(`${path}?revision=3`, 4)).toBeUndefined()
})
