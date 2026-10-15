import type { LoraDatasetOperationResponse, MusicLoraSource, MusicLoraTrainingSample } from '../api/generated'

export function trainingSampleKey(sample: MusicLoraSource): string {
  return sample.dataset ? `${sample.dataset.dataset_id}-${sample.dataset.sample_id}` : String(sample.track_id)
}
export function savedTrainingSamples(snapshot: LoraDatasetOperationResponse, engine: 'stable_audio3' | 'heartmula' | 'yue2', limit: number): Array<MusicLoraTrainingSample & { title: string }> {
  return snapshot.samples.filter(sample => sample.labeled).slice(0, Math.max(0, Math.min(100, limit))).map(sample => ({
    dataset: { dataset_id: snapshot.dataset.id, revision: snapshot.dataset.revision, sample_id: sample.id },
    caption: sample.prompt_override === 'genre' ? sample.genre ?? '' : sample.caption ?? '',
    lyrics: engine === 'heartmula' && !sample.is_instrumental ? sample.raw_lyrics || sample.lyrics || '' : '',
    instrumental: sample.is_instrumental ?? true,
    title: sample.filename || sample.id,
  }))
}
