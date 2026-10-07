import { afterEach, expect, it, vi } from 'vitest'
import { browserSupportReport, getEngineRuntime, getSupportReport, stopOwnedEngine, reviewedReportJson } from './support'

afterEach(() => vi.unstubAllGlobals())

it('rejects reports that contain private fields instead of downloading a redacted best guess', async () => {
  const report = browserSupportReport([], 'unreachable')
  expect(() => reviewedReportJson({ ...report, logs: '/private/key' })).toThrow()
  expect(() => reviewedReportJson({ ...report, recent_actions: [{ id: 'preview_report', outcome: 'failed', error_code: 'private key' }] })).toThrow()
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ...report, data_dir: '/Users/private' }))))
  await expect(getSupportReport()).rejects.toThrow('Invalid SupportReport')
})

it('browser fallback only reports coarse known platform fields and bounded typed actions', () => {
  const report = browserSupportReport(Array.from({ length: 30 }, () => ({ id: 'refresh_engines', outcome: 'completed' })), 'unreachable')
  const serialized = reviewedReportJson(report)
  expect(report.recent_actions).toHaveLength(20)
  expect(report.source).toBe('browser_fallback')
  expect(report.architecture).toBe('unknown')
  expect(report.physical_memory_bytes).toBeNull()
  expect(report.modules).toEqual([])
  expect(serialized).not.toContain(navigator.userAgent)
  expect(serialized).not.toContain('userAgent')
})

it('validates both engine inventory and exact stop identity through generated contracts', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(async () => new Response(JSON.stringify({ engines: [] })))
  vi.stubGlobal('fetch', fetch)
  await getEngineRuntime()
  await stopOwnedEngine({ engine_id: 'ace_step', instance_id: 'a'.repeat(32) })
  expect(fetch).toHaveBeenLastCalledWith('/api/support/engines/stop', expect.objectContaining({ method: 'POST', body: JSON.stringify({ engine_id: 'ace_step', instance_id: 'a'.repeat(32) }) }))
  await expect(stopOwnedEngine({ engine_id: 'ace_step', instance_id: '../private' })).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(2)
})
