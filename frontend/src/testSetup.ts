import { afterEach, beforeEach, expect, vi } from 'vitest'

const unexpectedRequests: string[] = []

beforeEach(() => {
  unexpectedRequests.length = 0
  const rejectFetch: typeof fetch = async input => {
    unexpectedRequests.push(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
    throw new Error('Unexpected test request: mock the API or fetch boundary.')
  }
  vi.stubGlobal('fetch', rejectFetch)
})

afterEach(() => {
  try { expect(unexpectedRequests, 'Tests must not make unmocked network requests.').toEqual([]) }
  finally { vi.unstubAllGlobals() }
})
