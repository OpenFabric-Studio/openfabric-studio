// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, watch, type App } from 'vue'
import { i18n, setLocale } from '../../i18n'
import AceSongFromBeatControls from './AceSongFromBeatControls.vue'
import type { AceSongConditioning } from '../../api/songFromBeat'
import type { MusicConditioningPreview, MusicConditioningSourceInfo } from '../../api/generated'
import * as api from '../../api/songFromBeat'
import * as sources from '../../api/musicConditioning'
vi.mock('../../api/songFromBeat', () => ({ songFromBeatCapabilities: vi.fn() }))
vi.mock('../../api/musicConditioning', () => ({ inspectConditioningSource: vi.fn() }))
vi.mock('./MusicTrainingSetup.vue', () => ({ default: defineComponent({ props: ['mode', 'engine', 'ready'], setup: props => () => h('div', { 'data-setup': true }, `${props.engine}:${props.mode}:${props.ready}`) }) }))
vi.mock('../../components/shared/MusicSourcePicker.vue', () => ({ default: defineComponent({ props: ['modelValue'], emits: ['inspected', 'update:modelValue'], setup: (props, { emit }) => {
  watch(() => props.modelValue, (value, previous) => { if (previous && !value) emit('inspected', null) }, { flush: 'sync' })
  return () => h('button', { onClick: () => { emit('update:modelValue', { source, audio_sha256: info.source_sha256 }); emit('inspected', info) } }, 'Choose fixture source')
} }) }))
const source = { kind: 'version', track_id: 1, version_id: 'a'.repeat(32) } as const
const info: MusicConditioningSourceInfo = { source, source_sha256: 'b'.repeat(64), source_frames: 960000, source_rate_hz: 48000, source_channels: 2, duration_sec: 20, source_bytes: 100000 }
const preview: MusicConditioningPreview = { ...info, required_free_bytes: 90000000, free_bytes: 4000000000, can_start: true }
let app: App | undefined
async function settle() { for (let n = 0; n < 12; n++) await nextTick() }
async function mount() {
  const props = reactive<{ mode: AceSongConditioning['mode'] | 'off'; preview: MusicConditioningPreview | null; fingerprint: string; instrumental: boolean; sourceDraft: { source: typeof source; label: string } | null }>({ mode: 'ace_complete', preview: null, fingerprint: '', instrumental: true, sourceDraft: null })
  const changes = vi.fn(), validity = vi.fn(), node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(AceSongFromBeatControls, { ...props, supported: true, onChange: changes, onValidity: validity, 'onUpdate:mode': value => { props.mode = value } }) }).use(i18n)
  app.mount(node); await settle(); return { node, props, changes, validity }
}
function button(node: ParentNode, text: string) { const result = [...node.querySelectorAll('button')].find(value => value.textContent === text); if (!result) throw new Error(`Missing ${text}`); return result }
beforeEach(() => {
  vi.resetAllMocks(); setLocale('en')
  vi.mocked(api.songFromBeatCapabilities).mockResolvedValue({ modes: (['ace_complete', 'ace_lego'] as const).map(mode => ({ mode, base_model: 'acestep-v15-base', installed: true, weights_ready: true, ready: true, proof_status: 'source_verified', source_commit: 'c'.repeat(40), proof_sha256: 'd'.repeat(64), runtime_verified: false, output_semantics: 'generated_full_mix', notes: 'Unverified quality.' })) })
  vi.mocked(sources.inspectConditioningSource).mockResolvedValue(info)
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
it('requires an explicit current queue forecast review and invalidates it after a brief or role change', async () => {
  const { node, props, changes, validity } = await mount()
  button(node, 'Choose fixture source').click(); await settle()
  expect(changes).toHaveBeenLastCalledWith({ mode: 'ace_complete', source, source_sha256: info.source_sha256, track_classes: ['bass', 'guitar'] })
  expect(validity).toHaveBeenLastCalledWith(false)
  props.preview = preview; props.fingerprint = 'e'.repeat(64); await settle()
  const checkbox = node.querySelector<HTMLInputElement>('[name="ace-song-reviewed"]'); if (!checkbox) throw new Error('Missing review')
  checkbox.click(); await settle(); expect(validity).toHaveBeenLastCalledWith(true)
  props.fingerprint = 'f'.repeat(64); await settle(); expect(validity).toHaveBeenLastCalledWith(false)
  expect(checkbox.checked).toBe(false)
  node.querySelector<HTMLInputElement>('[name="ace-role-vocals"]')?.click(); await settle()
  expect(node.textContent).toContain('Switch the brief')
  expect(changes).toHaveBeenLastCalledWith(null)
  expect(node.querySelector('[name="ace-song-reviewed"]')).toBeNull()
  expect(sources.inspectConditioningSource).not.toHaveBeenCalled()
})
it('keeps Complete and Lego payloads distinct and allows explicit text-only mode', async () => {
  const { node, props, changes, validity } = await mount()
  button(node, 'Choose fixture source').click(); await settle()
  props.mode = 'ace_lego'; await settle()
  expect(changes).toHaveBeenLastCalledWith({ mode: 'ace_lego', source, source_sha256: info.source_sha256, track_name: 'guitar' })
  props.mode = 'off'; await settle()
  expect(changes).toHaveBeenLastCalledWith(null); expect(validity).toHaveBeenLastCalledWith(true)
  expect(node.textContent).not.toContain('ace_step:song')
})
it('inspects a handoff without approving storage and ignores a response after teardown', async () => {
  let finish: ((value: MusicConditioningSourceInfo) => void) | undefined
  vi.mocked(sources.inspectConditioningSource).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { props, changes } = await mount()
  props.sourceDraft = { source, label: 'Saved beat' }; await settle()
  const signal = vi.mocked(sources.inspectConditioningSource).mock.calls[0]?.[1]
  app?.unmount(); app = undefined
  expect(signal?.aborted).toBe(true)
  finish?.(info); await settle(); expect(changes).not.toHaveBeenCalledWith(expect.objectContaining({ source }))
})
it('shows unsupported short sources without allowing the storage confirmation', async () => {
  const { node, props, validity } = await mount()
  vi.mocked(sources.inspectConditioningSource).mockResolvedValue({ ...info, duration_sec: 2, source_frames: 96000 })
  props.sourceDraft = { source, label: 'Short beat' }; await settle()
  props.preview = { ...preview, can_start: false, error_code: 'conditioning_source_invalid' }; props.fingerprint = 'e'.repeat(64); await settle()
  expect(node.textContent).toContain('10–300 seconds')
  expect(node.querySelector('[name="ace-song-reviewed"]')).toBeNull()
  expect(validity).toHaveBeenLastCalledWith(false)
})

it('replaces a picker-selected source with a later handoff without the picker reset aborting its inspection', async () => {
  const { node, props, changes } = await mount()
  button(node, 'Choose fixture source').click(); await settle()
  const second = { ...source, version_id: 'f'.repeat(32) }, secondInfo = { ...info, source: second, source_sha256: 'e'.repeat(64) }
  vi.mocked(sources.inspectConditioningSource).mockResolvedValue(secondInfo)
  props.sourceDraft = { source: second, label: 'Second source' }; await settle()
  expect(vi.mocked(sources.inspectConditioningSource).mock.calls.at(-1)?.[1]?.aborted).toBe(false)
  expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({ source: second, source_sha256: secondInfo.source_sha256 }))
})
