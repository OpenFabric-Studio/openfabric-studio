// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref, watch, type App } from 'vue'
import { i18n } from '../../i18n'
import * as api from '../../api/musicConditioning'
import type { LocalMusicRequest, MusicConditioningCapabilities, MusicConditioningSourceInfo } from '../../api/generated'
import MusicConditioningControls from './MusicConditioningControls.vue'

vi.mock('../../api/musicConditioning', async importOriginal => ({ ...await importOriginal<typeof import('../../api/musicConditioning')>(), conditioningCapabilities: vi.fn(), previewConditioning: vi.fn(), inspectConditioningSource: vi.fn() }))
vi.mock('./MusicTrainingSetup.vue', () => ({ default: defineComponent({ props: ['mode', 'variant'], setup: props => () => h('span', `Setup ${props.mode} ${props.variant}`) }) }))
vi.mock('../../components/shared/MusicSourcePicker.vue', () => ({ default: defineComponent({ props: ['modelValue'], emits: ['update:modelValue', 'inspected'], setup: (props, { emit }) => {
  watch(() => props.modelValue, (value, previous) => { if (previous && !value) emit('inspected', null) }, { flush: 'sync' })
  return () => h('button', { type: 'button', onClick: () => { emit('update:modelValue', { source: info.source, audio_sha256: info.source_sha256 }); emit('inspected', info) } }, 'Pick fixture')
} }) }))
const source = { kind: 'version', track_id: 42, version_id: 'a'.repeat(32) } as const
const info: MusicConditioningSourceInfo = { source, source_sha256: 'b'.repeat(64), source_bytes: 1000,
  source_rate_hz: 44100, source_channels: 2, source_frames: 220500, duration_sec: 5 }
const caps: MusicConditioningCapabilities = { modes: ['audio_to_audio', 'inpaint'].map(mode => ({ engine: 'stable_audio3', mode: mode === 'inpaint' ? 'inpaint' : 'audio_to_audio', proof_status: 'source_verified', enabled_small: true, enabled_medium: false,
  output_semantics: mode === 'inpaint' ? 'original_pcm_outside_region' : 'generated_full_mix', notes: 'Source verified; runtime unverified.' })) }
let app: App | undefined
async function settle() { for (let n = 0; n < 15; n++) await nextTick() }
async function mount(draft?: api.MusicConditioningDraft) {
  const request = ref<LocalMusicRequest>({ engine: 'stable_audio3', prompt: 'Piano', duration_seconds: 5, variant: 'small-music' })
  const sourceDraft = ref<api.MusicConditioningDraft | undefined>(draft)
  const validity = vi.fn(), change = vi.fn()
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(MusicConditioningControls, { request: request.value, supported: true, installed: true,
    sourceDraft: sourceDraft.value, onValidity: validity, onChange: change }) }).use(i18n)
  app.mount(node); await settle(); return { node, request, validity, change, sourceDraft }
}
function button(node: HTMLElement, label: string) { const found = [...node.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!found) throw new Error(`Missing ${label}`); return found }
async function mode(node: HTMLElement, value: string) { const select = node.querySelector('[name="conditioning-mode"]'); if (!(select instanceof HTMLSelectElement)) throw new Error('Missing mode'); select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); await settle() }
async function confirm(node: HTMLElement) { const input = node.querySelector('[name="conditioning-reviewed"]'); if (!(input instanceof HTMLInputElement)) throw new Error('Missing review checkbox'); input.checked = true; input.dispatchEvent(new Event('change', { bubbles: true })); await settle() }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.conditioningCapabilities).mockResolvedValue(caps); vi.mocked(api.previewConditioning).mockResolvedValue({ ...info, required_free_bytes: 5000, free_bytes: 10000, can_start: true }); vi.mocked(api.inspectConditioningSource).mockResolvedValue(info) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })

it('requires explicit canonical storage review and invalidates it when generation settings change', async () => {
  const { node, validity, change, request } = await mount()
  expect(validity).toHaveBeenLastCalledWith(true)
  await mode(node, 'audio_to_audio'); button(node, 'Pick fixture').click(); await settle()
  expect(validity).toHaveBeenLastCalledWith(false)
  button(node, 'Review reference and storage').click(); await settle()
  expect(api.previewConditioning).toHaveBeenCalledWith(expect.objectContaining({ conditioning: { mode: 'audio_to_audio', source, source_sha256: info.source_sha256, noise_level: .6 } }), expect.any(AbortSignal))
  expect(validity).toHaveBeenLastCalledWith(false)
  await confirm(node)
  expect(validity).toHaveBeenLastCalledWith(true)
  expect(change).toHaveBeenLastCalledWith({ mode: 'audio_to_audio', source, source_sha256: info.source_sha256, noise_level: .6 })
  request.value = { ...request.value, prompt: 'Different music' }; await settle()
  expect(validity).toHaveBeenLastCalledWith(false)
  expect(node.querySelector('[name="conditioning-reviewed"]')).toBeNull()
})

it('disables unavailable variant modes and exposes encoder setup without claiming runtime verification', async () => {
  const { node, request } = await mount()
  request.value = { ...request.value, variant: 'medium' }; await settle()
  const option = node.querySelector('option[value="audio_to_audio"]')
  expect(option instanceof HTMLOptionElement && option.disabled).toBe(true)
  expect(node.textContent).toContain('Setup conditioning medium')
  expect(node.textContent).toContain('runtime quality unverified')
})

it('inspects a beat handoff but never reviews or generates automatically', async () => {
  const beat = { kind: 'beat_render', beat_id: 'c'.repeat(32), beat_revision: 2, render_id: 'd'.repeat(32), sha256: info.source_sha256 } as const
  vi.mocked(api.inspectConditioningSource).mockResolvedValue({ ...info, source: beat })
  const { node, validity } = await mount({ source: beat, label: 'Retained beat mix' })
  expect(api.inspectConditioningSource).toHaveBeenCalledWith(beat, expect.any(AbortSignal))
  expect(api.previewConditioning).not.toHaveBeenCalled()
  expect(validity).toHaveBeenLastCalledWith(false)
  expect(node.textContent).toContain('Retained beat mix')
})

it('aborts stale preflight and ignores late success after a request change', async () => {
  let finish: ((value: Awaited<ReturnType<typeof api.previewConditioning>>) => void) | undefined
  vi.mocked(api.previewConditioning).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { node, request, validity } = await mount()
  await mode(node, 'audio_to_audio'); button(node, 'Pick fixture').click(); await settle()
  button(node, 'Review reference and storage').click(); await settle()
  const signal = vi.mocked(api.previewConditioning).mock.calls[0]?.[1]
  request.value = { ...request.value, seed: 10 }; await settle()
  expect(signal?.aborted).toBe(true)
  finish?.({ ...info, required_free_bytes: 5000, free_bytes: 10000, can_start: true }); await settle()
  expect(node.querySelector('[name="conditioning-reviewed"]')).toBeNull()
  expect(validity).toHaveBeenLastCalledWith(false)
})

it('keeps insufficient-space review blocked and requires a fresh checkbox after another review', async () => {
  vi.mocked(api.previewConditioning).mockResolvedValueOnce({ ...info, required_free_bytes: 5000,
    free_bytes: null, can_start: false, error_code: 'insufficient_disk' })
  const { node, validity } = await mount()
  await mode(node, 'audio_to_audio'); button(node, 'Pick fixture').click(); await settle()
  button(node, 'Review reference and storage').click(); await settle()
  expect(node.querySelector('[name="conditioning-reviewed"]')).toBeNull()
  expect(node.textContent).toContain('Free space is insufficient or unknown')
  expect(validity).toHaveBeenLastCalledWith(false)
  button(node, 'Review reference and storage').click(); await settle()
  expect(validity).toHaveBeenLastCalledWith(false)
  await confirm(node)
  expect(validity).toHaveBeenLastCalledWith(true)
})

it('reviews a bounded inpaint mask and blocks a source beyond the native duration limit', async () => {
  const { node, validity } = await mount()
  await mode(node, 'inpaint'); button(node, 'Pick fixture').click(); await settle()
  button(node, 'Review reference and storage').click(); await settle()
  expect(api.previewConditioning).toHaveBeenCalledWith(expect.objectContaining({ conditioning: {
    mode: 'inpaint', source, source_sha256: info.source_sha256, start_sec: 0, end_sec: 1, crossfade_sec: .02 } }), expect.any(AbortSignal))
  await confirm(node)
  expect(validity).toHaveBeenLastCalledWith(true)
  await mode(node, 'audio_to_audio')
  const noise = node.querySelector('[name="conditioning-noise"]')
  if (!(noise instanceof HTMLInputElement)) throw new Error('Missing noise input')
  noise.value = '1'; noise.dispatchEvent(new Event('input', { bubbles: true })); await settle()
  expect(button(node, 'Review reference and storage').disabled).toBe(true)
  expect(validity).toHaveBeenLastCalledWith(false)
})

it('retains a new source handoff after resetting the previously selected picker version', async () => {
  const { node, sourceDraft, change } = await mount()
  await mode(node, 'audio_to_audio'); button(node, 'Pick fixture').click(); await settle()
  const second = { ...source, version_id: 'f'.repeat(32) }, secondInfo = { ...info, source: second, source_sha256: 'e'.repeat(64) }
  let finish: ((value: MusicConditioningSourceInfo) => void) | undefined
  vi.mocked(api.inspectConditioningSource).mockReturnValue(new Promise(resolve => { finish = resolve }))
  sourceDraft.value = { source: second, label: 'Second source' }; await settle()
  expect(vi.mocked(api.inspectConditioningSource).mock.calls.at(-1)?.[1]?.aborted).toBe(false)
  finish?.(secondInfo); await settle()
  expect(change).toHaveBeenLastCalledWith(expect.objectContaining({ source: second, source_sha256: secondInfo.source_sha256 }))
})
