import { apiFetch } from './http'
import {
  parseCreateLoraDatasetRequest, parseRenameLoraDatasetRequest, parseLoraDatasetRevision,
  parseLoraDatasetEntry, parseLoraDatasetsResponse, parseLoraWorkspace,
  parseScanLoraDatasetRequest, parseSaveLoraDatasetRequest, parseLoraDatasetOperationResponse,
  parsePreprocessLoraDatasetRequest, parseLoraDatasetTaskStarted,
  parseAutoLabelLoraDatasetRequest, parseUpdateLoraDatasetSampleRequest,
  parseRemoveLoraDatasetRecordsRequest, parseRemoveLoraDatasetRecordsResponse,
} from './generated'
import type {
  LoraDatasetEntry, LoraDatasetsResponse, LoraWorkspace, ScanLoraDatasetRequest,
  SaveLoraDatasetRequest, LoraDatasetOperationResponse, PreprocessLoraDatasetRequest, LoraDatasetTaskStarted,
  AutoLabelLoraDatasetRequest, UpdateLoraDatasetSampleRequest,
  LoraDatasetRecordRevision, RemoveLoraDatasetRecordsResponse,
} from './generated'

import { parseUploadDatasetFilesResponse } from './contracts'
import type { UploadDatasetFilesResponse } from './contracts'
export type { UploadDatasetFilesResponse } from './contracts'

export async function uploadDatasetFiles(datasetName: string, files: File[], datasetId?: string, revision?: number, signal?: AbortSignal): Promise<UploadDatasetFilesResponse> {
  const form = new FormData()
  form.append('dataset_name', datasetName)
  if (datasetId) {
    datasetUrl(datasetId)
    const checked = parseLoraDatasetRevision({ revision })
    form.append('dataset_id', datasetId); form.append('revision', String(checked.revision))
  }
  for (const f of files) form.append('files', f, f.name)
  return apiFetch('/api/lora-dataset/upload', {
    method: 'POST',
    body: form,
    signal,
  }, parseUploadDatasetFilesResponse)
}

const BASE = '/api/lora-datasets'
function datasetUrl(id: string): string {
  if (!/^[0-9a-f]{32}$/.test(id)) throw new TypeError('Invalid dataset identifier')
  return `${BASE}/${id}`
}
function write<T>(url: string, body: object, parse: (value: unknown) => T, signal?: AbortSignal, method = 'POST', keepalive = false): Promise<T> {
  return apiFetch(url, { method, signal, keepalive, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, parse)
}
export function listLoraDatasets(signal?: AbortSignal): Promise<LoraDatasetsResponse> { return apiFetch(BASE, { signal }, parseLoraDatasetsResponse) }
export function getLoraWorkspace(signal?: AbortSignal): Promise<LoraWorkspace> { return apiFetch(`${BASE}/workspace`, { signal }, parseLoraWorkspace) }
export async function saveLoraWorkspace(workspace: LoraWorkspace, signal?: AbortSignal, keepalive = false): Promise<LoraWorkspace> { return write(`${BASE}/workspace`, parseLoraWorkspace(workspace), parseLoraWorkspace, signal, 'PUT', keepalive) }
export async function createLoraDataset(name: string, signal?: AbortSignal): Promise<LoraDatasetEntry> { return write(BASE, parseCreateLoraDatasetRequest({ name }), parseLoraDatasetEntry, signal) }
export async function renameLoraDataset(id: string, name: string, revision: number, signal?: AbortSignal): Promise<LoraDatasetEntry> { return write(`${datasetUrl(id)}/rename`, parseRenameLoraDatasetRequest({ name, revision }), parseLoraDatasetEntry, signal) }
export async function trashLoraDataset(id: string, revision: number, signal?: AbortSignal): Promise<LoraDatasetEntry> { return write(`${datasetUrl(id)}/trash`, parseLoraDatasetRevision({ revision }), parseLoraDatasetEntry, signal) }
export async function restoreLoraDataset(id: string, revision: number, signal?: AbortSignal): Promise<LoraDatasetEntry> { return write(`${datasetUrl(id)}/restore`, parseLoraDatasetRevision({ revision }), parseLoraDatasetEntry, signal) }
export async function removeLoraDatasetRecords(records: LoraDatasetRecordRevision[], signal?: AbortSignal): Promise<RemoveLoraDatasetRecordsResponse> { return write(`${BASE}/remove-records`, parseRemoveLoraDatasetRecordsRequest({ records }), parseRemoveLoraDatasetRecordsResponse, signal) }
export async function loadLoraDataset(id: string, revision: number, signal?: AbortSignal): Promise<LoraDatasetOperationResponse> { return write(`${datasetUrl(id)}/load`, parseLoraDatasetRevision({ revision }), parseLoraDatasetOperationResponse, signal) }
export async function scanLoraDataset(id: string, request: ScanLoraDatasetRequest, signal?: AbortSignal): Promise<LoraDatasetOperationResponse> { return write(`${datasetUrl(id)}/scan`, parseScanLoraDatasetRequest(request), parseLoraDatasetOperationResponse, signal) }
export async function saveLoraDataset(id: string, request: SaveLoraDatasetRequest, signal?: AbortSignal): Promise<LoraDatasetOperationResponse> { return write(`${datasetUrl(id)}/save`, parseSaveLoraDatasetRequest(request), parseLoraDatasetOperationResponse, signal) }
export async function preprocessLoraDataset(id: string, request: PreprocessLoraDatasetRequest, signal?: AbortSignal): Promise<LoraDatasetTaskStarted> { return write(`${datasetUrl(id)}/preprocess`, parsePreprocessLoraDatasetRequest(request), parseLoraDatasetTaskStarted, signal) }
export async function autoLabelLoraDataset(id: string, request: AutoLabelLoraDatasetRequest, signal?: AbortSignal): Promise<LoraDatasetTaskStarted> { return write(`${datasetUrl(id)}/auto-label`, parseAutoLabelLoraDatasetRequest(request), parseLoraDatasetTaskStarted, signal) }
export async function updateLoraDatasetSample(id: string, index: number, request: UpdateLoraDatasetSampleRequest, signal?: AbortSignal): Promise<LoraDatasetOperationResponse> {
  if (!Number.isSafeInteger(index) || index < 0 || index > 100000) throw new TypeError('Invalid sample index')
  return write(`${datasetUrl(id)}/samples/${index}`, parseUpdateLoraDatasetSampleRequest(request), parseLoraDatasetOperationResponse, signal)
}

export function readSavedLoraDataset(id: string, revision: number, signal?: AbortSignal): Promise<LoraDatasetOperationResponse> {
  const checked = parseLoraDatasetRevision({ revision })
  return apiFetch(`${datasetUrl(id)}/saved?revision=${checked.revision}`, { signal }, parseLoraDatasetOperationResponse)
}
