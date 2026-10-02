// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import JobCard from './JobCard.vue'
import type { AceJob } from '../../stores/aceStep'
import { i18n, setLocale } from '../../i18n'
import { ApiError } from '../../api/http'
import { VoiceApplyError } from '../../api/voices'
import { parseSavedTrack } from '../../api/contracts'
import * as tracksApi from '../../api/tracks'

const actions = vi.hoisted(() => ({ cancel: vi.fn(), removeJob: vi.fn(), retryVoiceReplacement: vi.fn(), renameJob: vi.fn(), requestInsertParams: vi.fn() }))
vi.mock('../../stores/aceStep', async (original) => ({ ...await original<typeof import('../../stores/aceStep')>(), useAceStepStore: () => actions }))
vi.mock('../../components/shared/StemsPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('../../components/shared/MidiPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('../../components/shared/WaveformPlayer.vue', () => ({ default: { props: ['src'], template: '<audio :src="src"></audio>' } }))
vi.mock('../../api/audioVersions', () => ({ listAudioVersions: vi.fn().mockResolvedValue({ track_id:42, original_available:false, versions:[] }) }))
vi.mock('../../api/audioExports', () => ({ listAudioExports: vi.fn().mockResolvedValue({ exports:[] }) }))
vi.mock('../../api/voices', async original => ({ ...await original<typeof import('../../api/voices')>(), listVoices: vi.fn().mockResolvedValue([]) }))
vi.mock('../../api/tracks', async original => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn().mockResolvedValue([]) }))

let app: App | undefined
beforeEach(() => {
  vi.clearAllMocks(); setLocale('en'); actions.cancel.mockResolvedValue(undefined); actions.retryVoiceReplacement.mockResolvedValue(undefined)
  vi.mocked(tracksApi.listTracks).mockResolvedValue([parseSavedTrack({ id: 42, short_id: 42, model: 'upload', title: 'Song · My voice', lyrics: '', seed: null, duration_ms: null, wall_ms: null, params: {}, filename: 'replacement.wav', audio_url: '/api/tracks/42/audio', abc_url: null, stems: null, midi: null, created_at: '2026-10-01T10:00:00Z', is_favorite: false })])
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
function replacement(): AceJob {
  return { id: 'saved_42', origin: 'upload', status: 'done', createdAt: Date.now(), title: 'Song · My voice', audioFormat: 'wav', batchSize: 1, lyrics: '', finalized: true, progress: 100, audioUrls: [], dbIds: [42], shortIds: [42],
    voiceId: 'a'.repeat(32), voiceName: 'My voice', voiceApply: 'running', voicePhase: 'separating', params: { source: 'voice_replacement', source_track_id: 41, source_filename: 'original.wav', task_type: 'voice_replacement' } }
}
function mount(job: AceJob) {
  app = createApp({ render: () => h(JobCard, { job, number: '42', view: 'cards' }) }).use(createPinia()).use(i18n)
  app.component('RouterLink', { props:['to'], template:'<a :href="to"><slot /></a>' })
  const container = document.body.appendChild(document.createElement('div')); app.mount(container); return container
}
function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll('button')].find((node) => node.textContent?.trim() === label || node.getAttribute('title') === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}

it('keeps the original available while replacement runs and offers conversion cancellation', () => {
  const container = mount(replacement())
  expect(container.querySelector('audio')).toBeNull()
  expect(container.querySelector('a[href="/api/tracks/41/audio"]')?.hasAttribute('download')).toBe(true)
  expect(container.textContent).toContain('Voice Replacement')
  button(container, 'Cancel').click()
  expect(actions.cancel).toHaveBeenCalledWith('saved_42')
  expect(actions.removeJob).not.toHaveBeenCalled()
})

it('shows replacement failure and retry even when no converted audio exists', () => {
  const container = mount({ ...replacement(), voiceApply: 'failed', voiceErrorCode: 'convert_failed' })
  expect(container.querySelector('[role=alert]')?.textContent).toBeTruthy()
  expect(container.textContent).toContain('Failed')
  button(container, 'Retry voice replacement').click()
  expect(actions.retryVoiceReplacement).toHaveBeenCalledWith('saved_42')
})

it('plays the converted result separately from the original and avoids ACE parameter-copy actions', () => {
  const container = mount({ ...replacement(), voiceApply: 'done', audioUrls: ['/api/tracks/42/audio?v=2'] })
  expect(container.querySelector('audio')?.getAttribute('src')).toBe('/api/tracks/42/audio?v=2')
  expect(container.querySelector('a[href="/api/tracks/41/audio"]')?.hasAttribute('download')).toBe(true)
  expect(container.textContent).toContain('Voice Replacement')
  expect(container.textContent).not.toContain('Copy params to form')
})

it('shows cancellation separately from conversion failure and keeps retry available', () => {
  const container = mount({ ...replacement(), voiceApply: 'failed', voiceErrorCode: 'cancelled' })
  expect(container.textContent).toContain('Voice replacement cancelled.')
  expect(container.textContent).toContain('Cancelled')
  expect(container.textContent).not.toContain('Failed')
  expect(button(container, 'Retry voice replacement').disabled).toBe(false)
})

it.each([
  { error: new ApiError('apply_cleanup_failed', 409), message: 'could not stop safely' },
  { error: new VoiceApplyError('replacement_active', ''), message: 'already being submitted' },
])('translates structured replacement action errors: $message', async ({ error, message }) => {
  actions.cancel.mockRejectedValueOnce(error)
  const container = mount(replacement())
  button(container, 'Cancel').click()
  for (let index = 0; index < 5; index++) await nextTick()
  expect(container.querySelector('[role=alert]')?.textContent).toContain(message)
})
