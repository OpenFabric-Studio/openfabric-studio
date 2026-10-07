import { apiFetch, ApiError } from './http'
import { parseEngineRuntimeResponse, parseStopEngineRequest, parseSupportReport } from './generated'
import type { EngineRuntimeResponse, StopEngineRequest, SupportAction, SupportReport } from './generated'

export function getEngineRuntime(signal?: AbortSignal): Promise<EngineRuntimeResponse> {
  return apiFetch('/api/support/engines', { signal }, parseEngineRuntimeResponse)
}

export async function stopOwnedEngine(body: StopEngineRequest, signal?: AbortSignal): Promise<EngineRuntimeResponse> {
  return apiFetch('/api/support/engines/stop', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parseStopEngineRequest(body)) }, parseEngineRuntimeResponse)
}

export function getSupportReport(signal?: AbortSignal): Promise<SupportReport> {
  return apiFetch('/api/support/report', { signal }, parseSupportReport)
}

export function supportErrorCode(error: unknown): NonNullable<SupportAction['error_code']> {
  if (error instanceof TypeError && (error.message === 'Invalid SupportReport response' || error.message === 'Invalid EngineRuntimeResponse response')) return 'support_unavailable'
  if (error instanceof ApiError) {
    switch (error.message) {
      case 'engine_busy': case 'engine_changed': case 'engine_not_owned': case 'engine_inactive':
      case 'engine_state_unverified': case 'engine_stop_failed': case 'support_unavailable':
        return error.message
    }
    return 'support_unavailable'
  }
  return 'backend_unreachable'
}

export function fallbackConnection(error: unknown): SupportReport['backend_connection'] {
  return error instanceof ApiError || error instanceof TypeError && error.message === 'Invalid SupportReport response' ? 'unverified' : 'unreachable'
}

export function browserSupportReport(actions: SupportAction[], connection: SupportReport['backend_connection']): SupportReport {
  // Do not serialize the user agent, browser identifiers, URL, storage or
  // exception text. Browser strings cannot reliably identify a Mac chip.
  const agent = typeof navigator === 'undefined' ? '' : navigator.userAgent
  const platform: SupportReport['platform'] = /Windows/.test(agent) ? 'win32' : /Macintosh/.test(agent) ? 'darwin' : /Linux/.test(agent) ? 'linux' : 'unknown'
  const candidate: unknown = import.meta.env.VITE_OPENFABRIC_VERSION
  const version = typeof candidate === 'string' && /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(candidate) && candidate.length <= 50 ? candidate : null
  return parseSupportReport({ schema_version: 1, source: 'browser_fallback', app_version: version,
    python_version: null, platform, architecture: 'unknown', acceleration: 'unknown', physical_memory_bytes: null,
    backend_connection: connection, modules: [], engines: [], recent_actions: actions.slice(-20) })
}

export function reviewedReportJson(report: unknown): string {
  return JSON.stringify(parseSupportReport(report), null, 2)
}

export function downloadSupportReport(reviewedJson: string): void {
  // Revalidate the precise reviewed document before creating a local file.
  const value: unknown = JSON.parse(reviewedJson)
  reviewedReportJson(value)
  const url = URL.createObjectURL(new Blob([reviewedJson], { type: 'application/json' }))
  try {
    const link = document.createElement('a')
    link.href = url; link.download = 'openfabric-support-report.json'
    document.body.appendChild(link); link.click(); link.remove()
  } finally { URL.revokeObjectURL(url) }
}
