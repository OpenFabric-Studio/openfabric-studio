import { apiFetch } from './http'

import { parseUploadDatasetFilesResponse } from './contracts'
import type { UploadDatasetFilesResponse } from './contracts'
export type { UploadDatasetFilesResponse } from './contracts'

export async function uploadDatasetFiles(datasetName: string, files: File[]): Promise<UploadDatasetFilesResponse> {
  const form = new FormData()
  form.append('dataset_name', datasetName)
  for (const f of files) form.append('files', f, f.name)
  return apiFetch('/api/lora-dataset/upload', {
    method: 'POST',
    body: form,
  }, parseUploadDatasetFilesResponse)
}
