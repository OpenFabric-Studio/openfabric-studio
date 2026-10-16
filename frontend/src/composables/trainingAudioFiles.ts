export const TRAINING_AUDIO_ACCEPT = '.wav,.mp3,.flac'

export interface TrainingAudioFile { file: File; path: string }
export interface TrainingAudioFilesResult {
  files: TrainingAudioFile[]
  skipped: number
  unreadable: number
  truncated: boolean
}

const MAX_ENTRIES = 10_000
const MAX_DEPTH = 32

function addAudio(result: TrainingAudioFilesResult, file: File, path = file.webkitRelativePath || file.name): void {
  if (file.size > 0 && /\.(wav|mp3|flac)$/i.test(file.name)) result.files.push({ file, path: path.replace(/^\/+/, '') })
  else result.skipped++
}

export function selectedTrainingAudio(files: Iterable<File>): TrainingAudioFilesResult {
  const result: TrainingAudioFilesResult = { files: [], skipped: 0, unreadable: 0, truncated: false }
  let visited = 0
  for (const file of files) {
    if (visited++ >= MAX_ENTRIES) { result.truncated = true; break }
    addAudio(result, file)
  }
  return result
}

function isFileEntry(entry: FileSystemEntry): entry is FileSystemFileEntry {
  return entry.isFile && 'file' in entry && typeof entry.file === 'function'
}

function isDirectoryEntry(entry: FileSystemEntry): entry is FileSystemDirectoryEntry {
  return entry.isDirectory && 'createReader' in entry && typeof entry.createReader === 'function'
}

function readEntry<T>(signal: AbortSignal, read: (success: (value: T) => void, failure: () => void) => void): Promise<T> {
  signal.throwIfAborted()
  return new Promise<T>((resolve, reject) => {
    const cancelled = () => reject(new DOMException('File selection cancelled', 'AbortError'))
    const failure = () => {
      signal.removeEventListener('abort', cancelled)
      reject(new Error('training_audio_unreadable'))
    }
    signal.addEventListener('abort', cancelled, { once: true })
    try {
      read(value => {
        signal.removeEventListener('abort', cancelled)
        resolve(value)
      }, failure)
    } catch { failure() }
  })
}

export async function collectDroppedTrainingAudio(data: DataTransfer, signal: AbortSignal): Promise<TrainingAudioFilesResult> {
  signal.throwIfAborted()
  const result = selectedTrainingAudio([])
  const roots: Array<{ entry: FileSystemEntry } | { file: File }> = []
  // Drag data becomes protected after the event returns, before directory reads finish.
  const fallbackFiles: File[] = []
  for (const file of data.files) {
    if (fallbackFiles.length >= MAX_ENTRIES) { result.truncated = true; break }
    fallbackFiles.push(file)
  }
  for (let index = 0; index < data.items.length; index++) {
    if (index >= MAX_ENTRIES) { result.truncated = true; break }
    const item = data.items[index]
    if (!item) continue
    if (item.kind !== 'file') continue
    try {
      const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null
      const file = entry ? null : item.getAsFile()
      if (entry) roots.push({ entry })
      else if (file) roots.push({ file })
      else result.unreadable++
    } catch { result.unreadable++ }
  }
  if (roots.length === 0 && result.unreadable === 0) {
    for (const file of fallbackFiles) {
      if (roots.length >= MAX_ENTRIES) { result.truncated = true; break }
      roots.push({ file })
    }
  }

  let visited = 0
  async function visit(entry: FileSystemEntry, depth: number): Promise<void> {
    signal.throwIfAborted()
    if (visited >= MAX_ENTRIES) { result.truncated = true; return }
    visited++
    if (depth > MAX_DEPTH) { result.truncated = true; return }
    try {
      if (isFileEntry(entry)) {
        const file = await readEntry<File>(signal, (success, failure) => entry.file(success, failure))
        signal.throwIfAborted()
        addAudio(result, file, entry.fullPath || file.name)
      } else if (isDirectoryEntry(entry)) {
        const reader = entry.createReader()
        while (true) {
          const entries = await readEntry<FileSystemEntry[]>(signal, (success, failure) => reader.readEntries(success, failure))
          signal.throwIfAborted()
          if (entries.length === 0) break
          for (const child of entries) {
            if (visited >= MAX_ENTRIES) { result.truncated = true; return }
            await visit(child, depth + 1)
          }
        }
      } else result.unreadable++
    } catch {
      signal.throwIfAborted()
      result.unreadable++
    }
  }

  for (const root of roots) {
    signal.throwIfAborted()
    if (visited >= MAX_ENTRIES) { result.truncated = true; break }
    if ('entry' in root) await visit(root.entry, 0)
    else { visited++; addAudio(result, root.file) }
  }
  return result
}
