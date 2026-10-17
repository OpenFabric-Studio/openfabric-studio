// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import * as api from '../../api/musicTerms'
import type { MusicTermsSnapshot } from '../../api/generated'
import { musicTermsEn } from '../../locales/musicTerms'
import { moduleWorkspaceEn } from '../../locales/moduleWorkspace'
import MusicTermsCard from './MusicTermsCard.vue'

vi.mock('../../api/musicTerms', () => ({ getMusicTerms: vi.fn(), acceptMusicTerms: vi.fn(), updateMusicTermsSettings: vi.fn() }))
import { ApiError } from '../../api/http'
let app: App | undefined
const digest = 'a'.repeat(64)
function snapshot(overrides: Partial<MusicTermsSnapshot> = {}): MusicTermsSnapshot {
  return { engine: 'yue2', operation: 'generation', revision: 2, noncommercial_enabled: false, allowed: false,
    requirements: [{ component_id: 'yue-weights', name: 'YuE weights', scope: 'model', restriction: 'noncommercial', source_url: 'https://example.org/terms', terms_digest: digest, acknowledged: false }], ...overrides }
}
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot()) })
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
async function settle() { for (let index = 0; index < 15; index++) await nextTick() }
async function mount(operation: MusicTermsSnapshot['operation'] = 'generation') {
  const engine = ref<MusicTermsSnapshot['engine']>('yue2'), mode = ref(operation), blocked = vi.fn()
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(MusicTermsCard, { engine: engine.value, operation: mode.value, onBlocked: blocked }) }).use(createI18n({ legacy: false, locale: 'en', messages: { en: { musicTerms: musicTermsEn, moduleWorkspace: moduleWorkspaceEn } } }))
  app.mount(node); await settle(); return { node, engine, mode, blocked }
}
function button(node: HTMLElement, text: string) {
  const result = [...node.querySelectorAll('button')].find(element => element.textContent?.trim() === text)
  if (!result) throw new Error(`Missing button: ${text}`)
  return result
}
async function review(node: HTMLElement) {
  for (const input of node.querySelectorAll<HTMLInputElement>('input[type=checkbox]')) input.click()
  await settle()
}

it('requires explicit review and the NC experiment choice before accepting exact current terms', async () => {
  vi.mocked(api.acceptMusicTerms).mockResolvedValue(snapshot({ revision: 3, noncommercial_enabled: true, allowed: true, requirements: [{ ...snapshot().requirements[0], acknowledged: true }] }))
  const { node, blocked } = await mount()
  expect(blocked.mock.calls[0]).toEqual([false]); expect(blocked).toHaveBeenLastCalledWith(true)
  expect(node.textContent).toContain('Model weights'); expect(node.textContent).toContain('Non-commercial')
  expect(node.textContent).toContain('does not certify rights')
  expect(node.querySelector('a')?.getAttribute('href')).toBe('https://example.org/terms')
  expect(button(node, 'Accept reviewed terms').disabled).toBe(true)
  const first = node.querySelector('input'); if (!first) throw new Error('Missing review checkbox')
  first.click(); await settle(); expect(button(node, 'Accept reviewed terms').disabled).toBe(true)
  const second = node.querySelectorAll('input')[1]; if (!second) throw new Error('Missing NC choice')
  second.click(); await settle(); button(node, 'Accept reviewed terms').click(); await settle()
  expect(api.acceptMusicTerms).toHaveBeenCalledWith({ engine: 'yue2', operation: 'generation', expected_revision: 2, terms_digests: [digest], allow_noncommercial: true }, expect.any(AbortSignal))
  expect(blocked).toHaveBeenLastCalledWith(false)
  expect(node.textContent).toContain('Terms acknowledged')
})

it('requires review for conditional terms without enabling non-commercial experiments', async () => {
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ engine: 'stable_audio3', requirements: [{ ...snapshot().requirements[0], restriction: 'conditional', name: 'Stable Audio weights' }] }))
  vi.mocked(api.acceptMusicTerms).mockResolvedValue(snapshot({ engine: 'stable_audio3', allowed: true }))
  const { node, engine } = await mount(); engine.value = 'stable_audio3'; await settle()
  expect(node.querySelectorAll('input[type=checkbox]')).toHaveLength(1)
  await review(node); button(node, 'Accept reviewed terms').click(); await settle()
  expect(api.acceptMusicTerms).toHaveBeenLastCalledWith(expect.objectContaining({ allow_noncommercial: false }), expect.any(AbortSignal))
})

it('refreshes changed terms and requires a new review without submitting them automatically', async () => {
  vi.mocked(api.acceptMusicTerms).mockRejectedValue(new ApiError('music_terms_changed', 409))
  const { node, blocked } = await mount(); await review(node)
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ revision: 4, requirements: [{ ...snapshot().requirements[0], terms_digest: 'b'.repeat(64) }] }))
  button(node, 'Accept reviewed terms').click(); await settle()
  expect(node.textContent).toContain('Terms or settings changed')
  expect(button(node, 'Accept reviewed terms').disabled).toBe(true)
  expect(api.acceptMusicTerms).toHaveBeenCalledTimes(1); expect(api.getMusicTerms).toHaveBeenCalledTimes(2)
  expect(blocked).toHaveBeenLastCalledWith(true)
})

it('keeps initial offline failures informational and allows an explicit retry', async () => {
  vi.mocked(api.getMusicTerms).mockRejectedValueOnce(new Error('/private/secret'))
  const { node, blocked } = await mount()
  expect(blocked).toHaveBeenLastCalledWith(false)
  expect(node.textContent).toContain('Could not check publisher terms')
  expect(node.textContent).not.toContain('/private/secret')
  button(node, 'Retry').click(); await settle(); expect(blocked).toHaveBeenLastCalledWith(true)
})

it('refreshes an accepted card and requires explicit review after non-commercial experiments are disabled elsewhere', async () => {
  const acknowledged = { ...snapshot().requirements[0], acknowledged: true }
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ allowed: true, noncommercial_enabled: true, requirements: [acknowledged] }))
  const { node, blocked } = await mount()
  expect(blocked).toHaveBeenLastCalledWith(false)
  expect(node.querySelectorAll('input[type=checkbox]')).toHaveLength(0)
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ revision: 3, noncommercial_enabled: false, requirements: [acknowledged] }))
  button(node, 'Check current terms').click(); await settle()
  expect(api.getMusicTerms).toHaveBeenCalledTimes(2)
  expect(blocked).toHaveBeenLastCalledWith(true)
  expect(node.querySelectorAll('input[type=checkbox]')).toHaveLength(2)
  expect([...node.querySelectorAll<HTMLInputElement>('input[type=checkbox]')].every(input => !input.checked)).toBe(true)
  expect(button(node, 'Accept reviewed terms').disabled).toBe(true)
  expect(api.acceptMusicTerms).not.toHaveBeenCalled()
})

it('keeps accepted terms available when an explicit refresh is offline', async () => {
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ allowed: true, noncommercial_enabled: true, requirements: [{ ...snapshot().requirements[0], acknowledged: true }] }))
  const { node, blocked } = await mount()
  vi.mocked(api.getMusicTerms).mockRejectedValue(new Error('offline'))
  button(node, 'Check current terms').click(); await settle()
  expect(blocked).toHaveBeenLastCalledWith(false)
  expect(node.textContent).toContain('Terms acknowledged')
  expect(node.textContent).toContain('Could not check publisher terms')
  expect(button(node, 'Check current terms').disabled).toBe(false)
})

it('aborts old reads and ignores results for an engine that is no longer selected', async () => {
  let finish: ((value: MusicTermsSnapshot) => void) | undefined
  vi.mocked(api.getMusicTerms).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { engine, blocked, node } = await mount()
  const oldSignal = vi.mocked(api.getMusicTerms).mock.calls[0]?.[2]
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ engine: 'ace_step', allowed: true, requirements: [] }))
  engine.value = 'ace_step'; await settle(); expect(oldSignal?.aborted).toBe(true)
  finish?.(snapshot()); await settle()
  expect(blocked).toHaveBeenLastCalledWith(false); expect(node.textContent).not.toContain('YuE weights')
})

it('aborts acceptance on teardown and ignores its late result', async () => {
  let finish: ((value: MusicTermsSnapshot) => void) | undefined
  vi.mocked(api.acceptMusicTerms).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node, blocked } = await mount(); await review(node); button(node, 'Accept reviewed terms').click(); await settle()
  const signal = vi.mocked(api.acceptMusicTerms).mock.calls[0]?.[1], callCount = blocked.mock.calls.length
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  finish?.(snapshot({ allowed: true })); await settle()
  expect(blocked).toHaveBeenCalledTimes(callCount); expect(api.getMusicTerms).toHaveBeenCalledTimes(1)
})

it('offers the global disable choice only in setup and preserves acknowledgements when refreshed offline', async () => {
  const accepted = snapshot({ operation: 'setup', noncommercial_enabled: true, allowed: true, requirements: [{ ...snapshot().requirements[0], acknowledged: true }] })
  vi.mocked(api.getMusicTerms).mockResolvedValue(accepted)
  vi.mocked(api.updateMusicTermsSettings).mockResolvedValue({ revision: 3, noncommercial_enabled: false })
  const { node, blocked } = await mount('setup')
  vi.mocked(api.getMusicTerms).mockRejectedValueOnce(new Error('offline'))
  button(node, 'Disable non-commercial experiments').click(); await settle()
  expect(api.updateMusicTermsSettings).toHaveBeenCalledWith({ expected_revision: 2, noncommercial_enabled: false }, expect.any(AbortSignal))
  expect(blocked).toHaveBeenLastCalledWith(true)
  expect(node.textContent).toContain('Already acknowledged')
  expect(node.textContent).toContain('Could not check publisher terms')
})

it('keeps a previously accepted snapshot when a revision conflict cannot be refreshed', async () => {
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ operation: 'setup', allowed: true, noncommercial_enabled: true, requirements: [{ ...snapshot().requirements[0], acknowledged: true }] }))
  vi.mocked(api.updateMusicTermsSettings).mockRejectedValue(new ApiError('music_terms_revision_conflict', 409))
  const { node, blocked } = await mount('setup')
  vi.mocked(api.getMusicTerms).mockRejectedValueOnce(new Error('offline'))
  button(node, 'Disable non-commercial experiments').click(); await settle()
  expect(blocked).toHaveBeenLastCalledWith(false)
  expect(node.textContent).toContain('Terms acknowledged')
  expect(node.textContent).toContain('Could not check publisher terms')
})

it('ignores an acceptance result after the selected operation changes', async () => {
  let finish: ((value: MusicTermsSnapshot) => void) | undefined
  vi.mocked(api.acceptMusicTerms).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const { node, mode, blocked } = await mount(); await review(node); button(node, 'Accept reviewed terms').click(); await settle()
  const oldSignal = vi.mocked(api.acceptMusicTerms).mock.calls[0]?.[1]
  vi.mocked(api.getMusicTerms).mockResolvedValue(snapshot({ operation: 'training' }))
  mode.value = 'training'; await settle(); expect(oldSignal?.aborted).toBe(true)
  finish?.(snapshot({ operation: 'generation', allowed: true })); await settle()
  expect(blocked).toHaveBeenLastCalledWith(true)
  expect(node.textContent).toContain('Training'); expect(node.querySelector('input')?.checked).toBe(false)
})

it('aborts reads on teardown without starting another read when the response eventually arrives', async () => {
  let finish: ((value: MusicTermsSnapshot) => void) | undefined
  vi.mocked(api.getMusicTerms).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  const { blocked } = await mount()
  const signal = vi.mocked(api.getMusicTerms).mock.calls[0]?.[2], callCount = blocked.mock.calls.length
  app?.unmount(); app = undefined; expect(signal?.aborted).toBe(true)
  finish?.(snapshot()); await settle()
  expect(blocked).toHaveBeenCalledTimes(callCount); expect(api.getMusicTerms).toHaveBeenCalledTimes(1)
})
