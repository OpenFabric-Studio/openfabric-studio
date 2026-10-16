import { afterEach, expect, it, vi } from 'vitest'
import * as queue from './musicQueue'
import type { MusicQueuePreviewRequest } from './generated'

const brief = { title: '', description: 'Warm piano and brushed drums', lyrics: '', instrumental: true, duration_seconds: 120, bpm: null, key: '', language: '' }
const request: MusicQueuePreviewRequest = { brief, selections: [{ engine: 'ace_step', variations: 2 }] }
const readiness = { engine: 'ace_step', supported: true, installed: true, weights_ready: true, managed_status: 'stopped', owned_instance: null, ready: true, error_code: '', setup_href: '/settings#module-ace_step' }
afterEach(() => vi.unstubAllGlobals())
function response(value: unknown, status = 200) {
  const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(value), { status }))
  vi.stubGlobal('fetch', fetch); return fetch
}

it('reads readiness without loading a model and rejects unknown catalog engines', async () => {
  const fetch = response({ engines: [readiness], adapters: [] })
  const controller = new AbortController()
  await expect(queue.musicQueueCatalog(controller.signal)).resolves.toMatchObject({ engines: [readiness] })
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music-queue/catalog', expect.objectContaining({ signal: controller.signal }))
  response({ engines: [{ ...readiness, engine: 'imaginary' }], adapters: [] })
  await expect(queue.musicQueueCatalog()).rejects.toThrow()
})

it('validates bounded variation counts before preview can reach the server', async () => {
  const fetch = response({})
  await expect(queue.previewMusicQueue({ ...request, selections: [{ engine: 'ace_step', variations: 0 }] })).rejects.toThrow()
  await expect(queue.previewMusicQueue({ ...request, brief: { ...brief, duration_seconds: 0 } })).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
})

it('submits the original brief and ordered selections to a read-only preview', async () => {
  const fetch = response({})
  await expect(queue.previewMusicQueue({ brief, selections: [{ engine: 'yue2', variations: 3 }, { engine: 'ace_step', variations: 1 }] })).rejects.toThrow()
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/music-queue/preview', expect.objectContaining({ method: 'POST', body: JSON.stringify({ brief, selections: [{ engine: 'yue2', variations: 3 }, { engine: 'ace_step', variations: 1 }] }) }))
})

it('uses the durable snapshot and revisioned control endpoints', async () => {
  const snapshot = { revision: 8, status: 'paused', batches: [] }
  const fetch = response(snapshot)
  await expect(queue.getMusicQueue()).resolves.toMatchObject(snapshot)
  await queue.controlMusicQueue({ revision: 7, action: 'pause' })
  await queue.reorderMusicQueue({ revision: 7, batch_ids: ['a'.repeat(32)] })
  await queue.retryMusicQueue({ revision: 7, item_id: 'b'.repeat(32), mode: 'save' })
  await queue.cancelMusicQueueBatch({ revision: 7, batch_id: 'a'.repeat(32) })
  expect(fetch.mock.calls.map(call => call[0])).toEqual(['/api/music-queue', '/api/music-queue/control', '/api/music-queue/reorder', '/api/music-queue/retry', `/api/music-queue/batches/${'a'.repeat(32)}/cancel`])
  expect(fetch.mock.calls[1]?.[1]?.body).toBe(JSON.stringify({ revision: 7, action: 'pause' }))
})

it('rejects traversal IDs, invalid revisions and malformed snapshots at the boundary', async () => {
  const fetch = response({ revision: 1, status: 'pretending', batches: [] })
  await expect(queue.cancelMusicQueueBatch({ revision: 1, batch_id: '../private' })).rejects.toThrow()
  await expect(queue.controlMusicQueue({ revision: -1, action: 'start' })).rejects.toThrow()
  await expect(queue.retryMusicQueue({ revision: 1, item_id: 'b'.repeat(32), mode: 'inference' }, new AbortController().signal)).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(1)
})
