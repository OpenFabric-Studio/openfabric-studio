import { afterEach, expect, it, vi } from 'vitest'
import { releaseTask } from './aceStep'
import { emptyAceSettings } from '../composables/generationSnapshots'
afterEach(() => vi.unstubAllGlobals())
it('uploads source and style reference separately with validated full form metadata', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ task_id: 'a'.repeat(32), status: 'queued', queue_position: 0 }))); vi.stubGlobal('fetch', fetch)
  const source = new File(['source'], 'source.wav'); const style = new File(['style'], 'style.wav'); const settings = emptyAceSettings()
  await releaseTask({ prompt: 'jazz', use_cot_caption: false }, source, 'Jazz', null, style, settings)
  const body = fetch.mock.calls[0]?.[1]?.body
  expect(body).toBeInstanceOf(FormData)
  if (!(body instanceof FormData)) throw new Error('Multipart missing')
  expect(body.get('ctx_audio')).toMatchObject({ name: 'source.wav' }); expect(body.get('ref_audio')).toMatchObject({ name: 'style.wav' })
  expect(body.get('settings')).toBe(JSON.stringify(settings)); expect(body.get('params')).toContain('"use_cot_caption":false')
})
