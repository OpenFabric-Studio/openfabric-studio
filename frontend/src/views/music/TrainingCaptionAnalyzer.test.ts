// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, KeepAlive, nextTick, ref, type App } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../../i18n'
import * as api from '../../api/trainingCaptions'
import { ApiError } from '../../api/http'
import type { MusicLoraSource, TrainingCaptionJob, TrainingCaptionRequest } from '../../api/generated'
import TrainingCaptionAnalyzer from './TrainingCaptionAnalyzer.vue'

vi.mock('../../api/trainingCaptions', () => ({ listTrainingCaptions: vi.fn(), submitTrainingCaptions: vi.fn(), getTrainingCaptions: vi.fn(), cancelTrainingCaptions: vi.fn() }))
type Sample = MusicLoraSource & { title: string; caption: string }
const first: Sample = { track_id: 42, title: 'Verse recording', caption: '' }
const second: Sample = { track_id: 43, title: 'Chorus recording', caption: '' }
function job(request: TrainingCaptionRequest, status: TrainingCaptionJob['status'] = 'running'): TrainingCaptionJob {
  return { id: 'a'.repeat(32), status, request, items: request.samples.map(source => ({ source, status: status === 'done' ? 'done' : 'queued', caption: status === 'done' ? 'Warm acoustic guitar with gentle percussion' : '', error_code: null })), created_at: '2026-10-16T18:00:00Z', updated_at: '2026-10-16T18:00:00Z', error_code: null }
}
let app: App | undefined
async function settle() { for (let index = 0; index < 30; index++) await nextTick() }
function button(node: ParentNode, text: string): HTMLButtonElement {
  const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === text)
  if (!found) throw new Error(`Missing ${text}`)
  return found
}
async function mount(initial: Sample[] = [], initiallyDisabled = false) {
  const samples = ref(initial.map(sample => ({ ...sample }))), disabled = ref(initiallyDisabled), seconds = ref(12), shown = ref(true)
  const busy = vi.fn(), applied = vi.fn((value: { source: MusicLoraSource; caption: string }) => {
    const sample = samples.value.find(item => item.track_id === value.source.track_id && JSON.stringify(item.dataset) === JSON.stringify(value.source.dataset))
    if (sample && !sample.caption.trim()) sample.caption = value.caption
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings', component: { render: () => null } }, { path: '/', component: { render: () => null } }] }); await router.push('/')
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(KeepAlive, null, { default: () => shown.value ? h(TrainingCaptionAnalyzer, { samples: samples.value, disabled: disabled.value, segmentSeconds: seconds.value, onCaption: applied, onBusy: busy }) : null }) }).use(i18n).use(router)
  app.mount(node); await settle()
  return { node, samples, disabled, seconds, shown, busy, applied }
}
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks()
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [] })
  vi.mocked(api.submitTrainingCaptions).mockImplementation(async request => job(request, 'done'))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.useRealTimers() })

it('automatically analyzes added recordings after import and keeps existing captions', async () => {
  const view = await mount([{ ...first, caption: 'My carefully written description' }], true)
  view.samples.value.push({ ...second }); await settle()
  expect(api.submitTrainingCaptions).not.toHaveBeenCalled()
  view.disabled.value = false; await settle()
  expect(api.submitTrainingCaptions).toHaveBeenCalledExactlyOnceWith({ samples: [{ track_id: 43 }], segment_seconds: 12 }, expect.any(AbortSignal))
  expect(view.samples.value.map(item => item.caption)).toEqual(['My carefully written description', 'Warm acoustic guitar with gentle percussion'])
  expect(view.node.textContent).toContain('Review suggested captions')
})

it('allows opting out and manually analyzing missing captions', async () => {
  const view = await mount()
  const toggle = view.node.querySelector('input[type="checkbox"]')
  if (!(toggle instanceof HTMLInputElement)) throw new Error('Missing automatic caption option')
  toggle.checked = false; toggle.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  view.samples.value.push({ ...first }); await settle()
  expect(api.submitTrainingCaptions).not.toHaveBeenCalled()
  button(view.node, 'Analyze missing captions').click(); await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
})

it('preserves manual edits and ignores removed sources or changed excerpt settings', async () => {
  let complete: ((value: TrainingCaptionJob) => void) | undefined
  vi.mocked(api.submitTrainingCaptions).mockImplementation(request => new Promise(resolve => { complete = resolve; vi.mocked(api.getTrainingCaptions).mockResolvedValue(job(request, 'done')) }))
  const view = await mount([first, second])
  const sample = view.samples.value[0]
  if (!sample || !complete) throw new Error('Missing pending caption request')
  sample.caption = 'My live edit'; view.samples.value.splice(1, 1)
  complete(job({ samples: [{ track_id: 42 }, { track_id: 43 }], segment_seconds: 12 }, 'done')); await settle()
  expect(view.applied).not.toHaveBeenCalled()
  expect(sample.caption).toBe('My live edit')
  view.samples.value.push({ track_id: 44, title: 'Bridge', caption: '' }); await settle()
  view.seconds.value = 18
  complete(job({ samples: [{ track_id: 44 }], segment_seconds: 12 }, 'done')); await settle()
  expect(view.applied).not.toHaveBeenCalled()
})

it('reports partial failures without retrying endlessly and retries only missing captions', async () => {
  vi.mocked(api.submitTrainingCaptions).mockImplementationOnce(async request => {
    const result = job(request, 'partial')
    result.items = [{ source: { track_id: 42 }, status: 'done', caption: 'Warm guitar', error_code: null }, { source: { track_id: 43 }, status: 'failed', caption: '', error_code: 'training_caption_failed' }]
    return result
  })
  const view = await mount([first, second])
  expect(view.samples.value.map(item => item.caption)).toEqual(['Warm guitar', ''])
  expect(view.node.textContent).toContain('Chorus recording')
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
  button(view.node, 'Analyze missing captions').click(); await settle()
  expect(api.submitTrainingCaptions).toHaveBeenLastCalledWith({ samples: [{ track_id: 43 }], segment_seconds: 12 }, expect.any(AbortSignal))
})

it('reconnects to backend work after polling loses connection', async () => {
  vi.mocked(api.submitTrainingCaptions).mockImplementationOnce(async request => job(request))
  vi.mocked(api.getTrainingCaptions).mockRejectedValueOnce(new TypeError('network')).mockImplementation(async () => job({ samples: [{ track_id: 42 }], segment_seconds: 12 }, 'done'))
  const view = await mount([first])
  await vi.advanceTimersByTimeAsync(1500); await settle()
  expect(view.node.textContent).toContain('Reconnecting')
  expect(view.busy).toHaveBeenLastCalledWith(true)
  await vi.advanceTimersByTimeAsync(1500); await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
  expect(view.busy).toHaveBeenLastCalledWith(false)
})

it('pauses observation while inactive and resumes without submitting a second job', async () => {
  vi.mocked(api.submitTrainingCaptions).mockImplementationOnce(async request => job(request))
  vi.mocked(api.getTrainingCaptions).mockResolvedValue(job({ samples: [{ track_id: 42 }], segment_seconds: 12 }, 'done'))
  const view = await mount([first])
  view.shown.value = false; await settle(); await vi.advanceTimersByTimeAsync(6000)
  expect(api.getTrainingCaptions).not.toHaveBeenCalled()
  view.shown.value = true; await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
})

it('cancels remaining analysis while keeping completed captions and pauses automatic submission', async () => {
  vi.mocked(api.submitTrainingCaptions).mockImplementationOnce(async request => job(request))
  const cancelled = job({ samples: [{ track_id: 42 }, { track_id: 43 }], segment_seconds: 12 }, 'cancelled')
  cancelled.error_code = 'training_caption_cancelled'
  cancelled.items = [{ source: { track_id: 42 }, status: 'done', caption: 'Warm guitar', error_code: null }, { source: { track_id: 43 }, status: 'failed', caption: '', error_code: 'training_caption_cancelled' }]
  vi.mocked(api.cancelTrainingCaptions).mockResolvedValue(cancelled)
  const view = await mount([first, second])
  button(view.node, 'Cancel analysis').click(); await settle()
  expect(api.cancelTrainingCaptions).toHaveBeenCalledWith('a'.repeat(32), expect.any(AbortSignal))
  expect(view.samples.value.map(item => item.caption)).toEqual(['Warm guitar', ''])
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
  expect(view.node.textContent).not.toContain('Could not analyze')
  expect(view.node.textContent).not.toContain('Caption analysis could not start')
})

it('shows actionable safe errors without leaking backend exception text', async () => {
  vi.mocked(api.submitTrainingCaptions).mockRejectedValue(new ApiError('private /Users/secret/token', 422))
  const view = await mount([first])
  expect(view.node.textContent).toContain('Caption analysis could not start')
  expect(view.node.textContent).not.toContain('/Users/secret')
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
})

it('adopts an active backend job and applies only the matching dataset revision', async () => {
  const source = { dataset: { dataset_id: 'd'.repeat(32), revision: 3, sample_id: 'verse' } }
  const resumed = job({ samples: [source], segment_seconds: 12 })
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [resumed] })
  vi.mocked(api.getTrainingCaptions).mockResolvedValue(job(resumed.request, 'done'))
  const view = await mount([{ ...source, dataset: { ...source.dataset, revision: 4 }, title: 'New dataset revision', caption: '' }])
  expect(view.applied).not.toHaveBeenCalled()
})

it('does not repeatedly retry failed items from an adopted job', async () => {
  const request = { samples: [{ track_id: 42 }], segment_seconds: 12 }
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [job(request)] })
  const failed = job(request, 'failed'); failed.items = [{ source: { track_id: 42 }, status: 'failed', caption: '', error_code: 'training_caption_failed' }]
  vi.mocked(api.getTrainingCaptions).mockResolvedValue(failed)
  const view = await mount([first])
  await vi.advanceTimersByTimeAsync(5000); await settle()
  expect(api.submitTrainingCaptions).not.toHaveBeenCalled()
  expect(button(view.node, 'Analyze missing captions').disabled).toBe(false)
})

it('adopts a submitted job after its POST response is lost and ignores late responses after unmount', async () => {
  const request = { samples: [{ track_id: 42 }], segment_seconds: 12 }
  vi.mocked(api.submitTrainingCaptions).mockRejectedValueOnce(new TypeError('network'))
  const view = await mount([first])
  expect(view.node.textContent).toContain('Reconnecting')
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [job(request, 'done')] })
  await vi.advanceTimersByTimeAsync(1500); await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
  let finish: ((value: TrainingCaptionJob) => void) | undefined
  vi.mocked(api.submitTrainingCaptions).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  view.samples.value.push({ ...second }); await settle()
  if (!finish) throw new Error('Missing second caption request')
  app?.unmount(); app = undefined
  finish(job({ samples: [{ track_id: 43 }], segment_seconds: 12 }, 'done')); await settle()
  expect(view.samples.value[1]?.caption).toBe('')
})

it('recovers completed backend results when deactivated before POST returns', async () => {
  const request = { samples: [{ track_id: 42 }], segment_seconds: 12 }
  let finish: ((value: TrainingCaptionJob) => void) | undefined
  vi.mocked(api.submitTrainingCaptions).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const view = await mount([first])
  view.shown.value = false; await settle()
  if (!finish) throw new Error('Missing pending POST')
  finish(job(request, 'done')); await settle()
  expect(view.applied).not.toHaveBeenCalled()
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [job(request, 'done')] })
  view.shown.value = true; await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
  expect(view.busy).toHaveBeenLastCalledWith(false)
})

it('reuses saved completed captions for the same recordings and excerpt after remount', async () => {
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [job({ samples: [{ track_id: 42 }], segment_seconds: 12 }, 'done')] })
  const view = await mount([first])
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
  expect(api.submitTrainingCaptions).not.toHaveBeenCalled()
  const sample = view.samples.value[0]
  if (!sample) throw new Error('Missing restored recording')
  sample.caption = ''; await settle()
  expect(sample.caption).toBe('')
  button(view.node, 'Analyze missing captions').click(); await settle()
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
})

it('reconciles a successful POST with unreadable JSON instead of abandoning accepted work', async () => {
  const request = { samples: [{ track_id: 42 }], segment_seconds: 12 }
  vi.mocked(api.submitTrainingCaptions).mockRejectedValueOnce(new ApiError('Invalid response from server', 200))
  const view = await mount([first])
  expect(view.busy).toHaveBeenLastCalledWith(true)
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [job(request, 'done')] })
  await vi.advanceTimersByTimeAsync(1500); await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
  expect(api.submitTrainingCaptions).toHaveBeenCalledTimes(1)
})

it('recovers the exact pending job before observing unrelated active work', async () => {
  const request = { samples: [{ track_id: 42 }], segment_seconds: 12 }
  vi.mocked(api.submitTrainingCaptions).mockRejectedValueOnce(new TypeError('network'))
  const view = await mount([first])
  const other = { ...job({ samples: [{ track_id: 99 }], segment_seconds: 12 }), id: 'b'.repeat(32) }
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [other, job(request, 'done')] })
  await vi.advanceTimersByTimeAsync(1500); await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
  expect(view.busy).toHaveBeenLastCalledWith(true)
})

it('retains actionable setup errors while checking for an accepted server job', async () => {
  vi.mocked(api.submitTrainingCaptions).mockRejectedValueOnce(new ApiError('training_caption_setup_required', 503))
  const view = await mount([first])
  await vi.advanceTimersByTimeAsync(1500); await settle()
  expect(view.node.textContent).toContain('Prepare ACE-Step in Settings')
  expect(view.busy).toHaveBeenLastCalledWith(false)
})

it('applies recovered captions during another import before observing unrelated active work', async () => {
  const request = { samples: [{ track_id: 42 }], segment_seconds: 12 }
  vi.mocked(api.submitTrainingCaptions).mockRejectedValueOnce(new TypeError('network'))
  const view = await mount([first])
  view.disabled.value = true; await settle()
  const other = { ...job({ samples: [{ track_id: 99 }], segment_seconds: 12 }), id: 'b'.repeat(32) }
  vi.mocked(api.listTrainingCaptions).mockResolvedValue({ jobs: [other, job(request, 'done')] })
  await vi.advanceTimersByTimeAsync(1500); await settle()
  view.disabled.value = false; await settle()
  expect(view.samples.value[0]?.caption).toBe('Warm acoustic guitar with gentle percussion')
})

it('explains blocked model work when native cleanup cannot be confirmed', async () => {
  vi.mocked(api.submitTrainingCaptions).mockImplementationOnce(async request => ({ ...job(request, 'failed'), error_code: 'training_caption_stop_failed' }))
  const view = await mount([first])
  expect(view.node.textContent).toContain('Restart the app to retry cleanup before training')
})
