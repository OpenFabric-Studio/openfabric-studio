// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { collectDroppedTrainingAudio, selectedTrainingAudio, TRAINING_AUDIO_ACCEPT } from './trainingAudioFiles'

function audio(name: string, relativePath = '', content = 'audio'): File {
  const file = new File([content], name)
  Object.defineProperty(file, 'webkitRelativePath', { value: relativePath })
  return file
}

function fileEntry(file: File, path = `/${file.name}`, read?: FileSystemFileEntry['file']) {
  return {
    name: file.name, fullPath: path, isFile: true, isDirectory: false,
    file: read ?? ((success: FileCallback) => success(file)),
  }
}

function directory(name: string, batches: object[][], read?: FileSystemDirectoryReader['readEntries']) {
  let index = 0
  const readEntries = vi.fn(read ?? ((success: FileSystemEntriesCallback) => {
    const entries = batches[index++] ?? []
    // The browser's native callback receives these entry-shaped test fixtures.
    Reflect.apply(success, undefined, [entries])
  }))
  return {
    name, fullPath: `/${name}`, isFile: false, isDirectory: true,
    createReader: () => ({ readEntries }), readEntries,
  }
}

function dropped(entries: object[], files: File[] = []): DataTransfer {
  const data = new DataTransfer()
  for (const [index, entry] of entries.entries()) {
    data.items.add(files[index] ?? audio(`entry-${index}.wav`))
    const item = data.items[index]
    if (!item) throw new Error('Missing test drag item')
    Object.defineProperty(item, 'webkitGetAsEntry', { value: () => entry })
  }
  return data
}

describe('selectedTrainingAudio', () => {
  it('keeps supported nonempty music and preserves folder-relative display paths', () => {
    const wav = audio('Song.WAV', '/album/Song.WAV')
    const mp3 = audio('second.mp3')
    const flac = audio('third.FlAc')
    const result = selectedTrainingAudio([wav, mp3, flac, audio('notes.txt'), audio('empty.wav', '', '')])
    expect(TRAINING_AUDIO_ACCEPT).toBe('.wav,.mp3,.flac')
    expect(result).toEqual({ files: [
      { file: wav, path: 'album/Song.WAV' }, { file: mp3, path: 'second.mp3' }, { file: flac, path: 'third.FlAc' },
    ], skipped: 2, unreadable: 0, truncated: false })
  })

  it('bounds large picker selections to 10,000 files', () => {
    const files = Array.from({ length: 10_001 }, (_, index) => audio(`song-${index}.wav`))
    const result = selectedTrainingAudio(files)
    expect(result.files).toHaveLength(10_000)
    expect(result.truncated).toBe(true)
  })
})

describe('collectDroppedTrainingAudio', () => {
  it('collects several flat files while skipping unsupported and empty files', async () => {
    const files = [audio('one.wav'), audio('two.MP3'), audio('notes.txt'), audio('empty.flac', '', '')]
    const result = await collectDroppedTrainingAudio(dropped(files.map(file => fileEntry(file))), new AbortController().signal)
    expect(result.files.map(item => item.file.name)).toEqual(['one.wav', 'two.MP3'])
    expect(result.skipped).toBe(2)
    expect(result.unreadable).toBe(0)
    expect(result.truncated).toBe(false)
  })

  it('walks nested folders and retains their relative display paths', async () => {
    const first = audio('first.wav')
    const second = audio('second.flac')
    const nested = directory('drums', [[fileEntry(second, '/album/drums/second.flac')]])
    const album = directory('album', [[fileEntry(first, '/album/first.wav'), nested]])
    const result = await collectDroppedTrainingAudio(dropped([album]), new AbortController().signal)
    expect(result.files).toEqual([{ file: first, path: 'album/first.wav' }, { file: second, path: 'album/drums/second.flac' }])
    expect(album.readEntries).toHaveBeenCalledTimes(2)
    expect(nested.readEntries).toHaveBeenCalledTimes(2)
  })

  it('reads every batch from one reader, including folders with over 100 entries', async () => {
    const entries = Array.from({ length: 205 }, (_, index) => fileEntry(audio(`song-${index}.mp3`)))
    const folder = directory('collection', [entries.slice(0, 100), entries.slice(100, 200), entries.slice(200)])
    const result = await collectDroppedTrainingAudio(dropped([folder]), new AbortController().signal)
    expect(result.files).toHaveLength(205)
    expect(folder.readEntries).toHaveBeenCalledTimes(4)
    expect(result.truncated).toBe(false)
  })

  it('captures all root entries before the drag event becomes inaccessible', async () => {
    const first = audio('first.wav')
    const second = audio('second.wav')
    const data = dropped([fileEntry(first), fileEntry(second)])
    const resultPromise = collectDroppedTrainingAudio(data, new AbortController().signal)
    data.items.clear()
    expect((await resultPromise).files.map(item => item.file.name)).toEqual(['first.wav', 'second.wav'])
  })

  it('keeps readable peers after file and directory errors', async () => {
    const brokenFile = fileEntry(audio('broken.wav'), '/broken.wav', (_success, fail) => fail?.(new DOMException('private path')))
    const brokenFolder = directory('unreadable', [], (_success, fail) => fail?.(new DOMException('private path')))
    const good = audio('good.mp3')
    const result = await collectDroppedTrainingAudio(dropped([brokenFile, brokenFolder, fileEntry(good)]), new AbortController().signal)
    expect(result).toEqual({ files: [{ file: good, path: 'good.mp3' }], skipped: 0, unreadable: 2, truncated: false })
  })

  it('keeps earlier directory batches when a later read fails', async () => {
    const good = audio('good.mp3')
    let reads = 0
    const folder = directory('partial', [], (success, fail) => {
      if (reads++ === 0) Reflect.apply(success, undefined, [[fileEntry(good, '/partial/good.mp3')]])
      else fail?.(new DOMException('private path'))
    })
    const result = await collectDroppedTrainingAudio(dropped([folder]), new AbortController().signal)
    expect(result.files).toEqual([{ file: good, path: 'partial/good.mp3' }])
    expect(result.unreadable).toBe(1)
  })

  it('falls back to the synchronously captured FileList when entry APIs are absent', async () => {
    const data = new DataTransfer()
    const file = audio('chosen.wav')
    data.items.add(file)
    const item = data.items[0]
    if (!item) throw new Error('Missing test drag item')
    Object.defineProperty(item, 'webkitGetAsEntry', { value: undefined })
    const resultPromise = collectDroppedTrainingAudio(data, new AbortController().signal)
    data.items.clear()
    expect((await resultPromise).files).toEqual([{ file, path: 'chosen.wav' }])
  })

  it('uses getAsFile when an individual entry is unavailable', async () => {
    const file = audio('chosen.wav')
    const data = new DataTransfer()
    data.items.add(file)
    const item = data.items[0]
    if (!item) throw new Error('Missing test drag item')
    Object.defineProperty(item, 'webkitGetAsEntry', { value: () => null })
    expect((await collectDroppedTrainingAudio(data, new AbortController().signal)).files).toEqual([{ file, path: 'chosen.wav' }])
  })

  it('accepts a FileList-only drop from a browser without drag items', async () => {
    const file = audio('chosen.wav')
    const data = new DataTransfer()
    Object.defineProperty(data, 'files', { value: [file] })
    const result = await collectDroppedTrainingAudio(data, new AbortController().signal)
    expect(result.files).toEqual([{ file, path: 'chosen.wav' }])
  })

  it('rejects an already cancelled drop before reading entries', async () => {
    const folder = directory('album', [])
    const controller = new AbortController()
    controller.abort()
    await expect(collectDroppedTrainingAudio(dropped([folder]), controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    expect(folder.readEntries).not.toHaveBeenCalled()
  })

  it('cancels an outstanding read promptly and never traverses later entries', async () => {
    let finish: FileSystemEntriesCallback | undefined
    const waiting = directory('waiting', [], success => { finish = success })
    const later = directory('later', [])
    const controller = new AbortController()
    const result = collectDroppedTrainingAudio(dropped([waiting, later]), controller.signal)
    controller.abort()
    await expect(result).rejects.toMatchObject({ name: 'AbortError' })
    finish?.([])
    expect(later.readEntries).not.toHaveBeenCalled()
    expect(waiting.readEntries).toHaveBeenCalledTimes(1)
  })

  it('bounds traversal to 10,000 entries and reports truncation', async () => {
    const entries = Array.from({ length: 10_001 }, (_, index) => fileEntry(audio(`song-${index}.wav`)))
    const folder = directory('large', [entries])
    const result = await collectDroppedTrainingAudio(dropped([folder]), new AbortController().signal)
    expect(result.files).toHaveLength(9_999)
    expect(result.truncated).toBe(true)
    expect(folder.readEntries).toHaveBeenCalledTimes(1)
  })

  it('bounds directory depth while preserving accessible peers', async () => {
    let nested: object = fileEntry(audio('too-deep.wav'))
    for (let index = 33; index >= 0; index--) nested = directory(`level-${index}`, [[nested]])
    const good = audio('peer.wav')
    const result = await collectDroppedTrainingAudio(dropped([nested, fileEntry(good)]), new AbortController().signal)
    expect(result.files).toEqual([{ file: good, path: 'peer.wav' }])
    expect(result.truncated).toBe(true)
  })

  it('counts depth-limited entries toward the traversal budget', async () => {
    const children = Array.from({ length: 10_001 }, (_, index) => fileEntry(audio(`deep-${index}.wav`)))
    let nested: object = directory('last-directory', [children])
    for (let index = 31; index >= 0; index--) nested = directory(`level-${index}`, [[nested]])
    const result = await collectDroppedTrainingAudio(dropped([nested, fileEntry(audio('after-budget.wav'))]), new AbortController().signal)
    expect(result.files).toEqual([])
    expect(result.truncated).toBe(true)
  })

  it('bounds synchronous capture even when all root entries are unreadable', async () => {
    const data = new DataTransfer()
    const failedEntry = vi.fn(() => { throw new DOMException('private path') })
    for (let index = 0; index < 10_001; index++) {
      data.items.add(audio(`broken-${index}.wav`))
      Object.defineProperty(data.items[index], 'webkitGetAsEntry', { value: failedEntry })
    }
    const result = await collectDroppedTrainingAudio(data, new AbortController().signal)
    expect(failedEntry).toHaveBeenCalledTimes(10_000)
    expect(result.unreadable).toBe(10_000)
    expect(result.truncated).toBe(true)
  })
})
