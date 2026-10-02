import { apiFetch, apiJson } from './http'
import type { TimelineProject } from '../audio/timelineTypes'

import { parseProjectsResponse, parseProjectFullResponse } from './contracts'
import type { ProjectSummaryResponse } from './contracts'
import { parseTimelineProject } from './editorValidation'
export { parseTimelineProject } from './editorValidation'

export type ProjectSummary = ProjectSummaryResponse
export interface ProjectFull extends ProjectSummary {
  data: TimelineProject
}

function parseProjectFull(value: unknown): ProjectFull {
  const row = parseProjectFullResponse(value)
  return { ...row, data: parseTimelineProject(row.data) }
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const json = await apiFetch('/api/projects', undefined, parseProjectsResponse)
  return json.data
}

export function createProject(name: string, data: TimelineProject): Promise<ProjectFull> {
  return apiJson('/api/projects', { name, data: parseTimelineProject(data) }, 'POST', parseProjectFull)
}

export function getProject(id: number): Promise<ProjectFull> {
  return apiFetch(`/api/projects/${id}`, undefined, parseProjectFull)
}

export function updateProject(id: number, patch: { name?: string; data?: TimelineProject }): Promise<ProjectFull> {
  return apiJson(`/api/projects/${id}`, { ...patch, ...(patch.data === undefined ? {} : { data: parseTimelineProject(patch.data) }) }, 'PUT', parseProjectFull)
}

export async function deleteProject(id: number): Promise<void> {
  await apiFetch(`/api/projects/${id}`, { method: 'DELETE' })
}
