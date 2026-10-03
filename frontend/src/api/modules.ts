import { apiFetch } from './http'
import { parseModuleInventory, parseModulePlan, parseModuleInstallJob, parseModuleJobsResponse } from './generated'
import type { ModuleInventory, ModulePlan, ModulePlanRequest, ModuleInstallRequest, ModuleInstallJob } from './generated'

export function getModules(signal?: AbortSignal): Promise<ModuleInventory> {
  return apiFetch('/api/modules', { signal }, parseModuleInventory)
}
export function planModules(body: ModulePlanRequest, signal?: AbortSignal): Promise<ModulePlan> {
  return apiFetch('/api/modules/plan', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, parseModulePlan)
}
export function installModules(body: ModuleInstallRequest, signal?: AbortSignal): Promise<ModuleInstallJob> {
  return apiFetch('/api/modules/jobs', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, parseModuleInstallJob)
}
export async function listModuleJobs(signal?: AbortSignal): Promise<ModuleInstallJob[]> {
  return (await apiFetch('/api/modules/jobs', { signal }, parseModuleJobsResponse)).jobs
}
export function controlModuleJob(id: string, action: 'cancel' | 'resume', signal?: AbortSignal): Promise<ModuleInstallJob> {
  return apiFetch(`/api/modules/jobs/${encodeURIComponent(id)}/${action}`, { method: 'POST', signal }, parseModuleInstallJob)
}
