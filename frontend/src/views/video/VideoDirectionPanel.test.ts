// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import en from '../../locales/en'
import { videoDirectionEn } from '../../locales/videoDirection'
import type { VideoProject, VideoReadinessResponse } from '../../api/contracts'
import type { VideoDraft } from './useVideoWorkspace'
import { videoProjectFixture, videoReadinessFixture } from './videoFixtures'
import VideoDirectionPanel from './VideoDirectionPanel.vue'

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })

function draftFixture(mode: VideoDraft['mode'] = 'generated'): VideoDraft {
  return { name: 'Direction draft', mode, direction: 'A quiet neon city', character_lock: false, seed: 17,
    settings: { engine_pack: 'ltx23', width: 704, height: 448, stage1_steps: 30, stage2_steps: 3, cfg_scale: 3, negative_prompt: 'blurry' },
    export_settings: { aspect: 'landscape', quality: 'standard', include_overlays: true }, shots: [], markers: [], overlays: [] }
}
async function flush() { await nextTick(); await nextTick() }
async function mount(options: { mode?: VideoDraft['mode']; project?: VideoProject; readiness?: VideoReadinessResponse | null; readOnly?: boolean; canAnalyze?: boolean } = {}) {
  const draft = ref(draftFixture(options.mode))
  const project = ref(options.project ?? videoProjectFixture())
  const readiness = ref(options.readiness === undefined ? videoReadinessFixture : options.readiness)
  const readOnly = ref(options.readOnly ?? false)
  const canAnalyze = ref(options.canAnalyze ?? true)
  const analyze = vi.fn(), advance = vi.fn(), duplicate = vi.fn(), upload = vi.fn<(file: File) => void>()
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en: { ...en, videoDirection: videoDirectionEn } } })
  app = createApp({ render: () => h(VideoDirectionPanel, { modelValue: draft.value, 'onUpdate:modelValue': (value: VideoDraft) => { draft.value = value },
    project: project.value, readiness: readiness.value, readOnly: readOnly.value, canAnalyze: canAnalyze.value,
    onAnalyze: analyze, onContinue: advance, onDuplicate: duplicate, onUpload: upload }) })
  app.use(i18n).mount(document.body.appendChild(document.createElement('div')))
  await flush()
  return { draft, project, readiness, readOnly, canAnalyze, analyze, advance, duplicate, upload }
}
function button(name: string): HTMLButtonElement {
  const found = [...document.querySelectorAll('button')].find(item => item.getAttribute('aria-label') === name || item.textContent?.trim() === name)
  if (!found) throw new Error(`Missing button: ${name}`)
  return found
}
function field<T extends HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(selector: string, constructor: { new(): T }): T {
  const found = document.querySelector(selector)
  if (!(found instanceof constructor)) throw new Error(`Missing field: ${selector}`)
  return found
}
function details(text: string): HTMLDetailsElement {
  const found = [...document.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === text)
  if (!found) throw new Error(`Missing disclosure: ${text}`)
  return found
}
async function selectFile(file: File) {
  const input = field('input[type=file]', HTMLInputElement)
  Object.defineProperty(input, 'files', { configurable: true, value: [file] })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flush()
}

it('presents three named approach buttons with selected state and purpose', async () => {
  await mount()
  const choices = document.querySelectorAll('[data-video-approach]')
  expect(choices).toHaveLength(3)
  expect(button('Generated scenes').getAttribute('aria-pressed')).toBe('true')
  expect(button('Animated cover').getAttribute('aria-pressed')).toBe('false')
  expect(button('Audio visualizer').getAttribute('aria-pressed')).toBe('false')
  expect(document.querySelector('[role=tablist]')).toBeNull()
  expect(button('Generated scenes').textContent).toContain('Create new scenes')
  expect(button('Animated cover').textContent).toContain('gentle camera motion')
  expect(button('Audio visualizer').textContent).toContain('audio-reactive')
})

it('keeps direction and model visible while moving generation tuning and duplication into closed disclosures', async () => {
  await mount()
  expect(field('[data-video-direction-prompt]', HTMLTextAreaElement).closest('details')).toBeNull()
  expect(field('[data-video-model]', HTMLSelectElement).closest('details')).toBeNull()
  const advanced = details('Advanced options')
  expect(advanced.open).toBe(false)
  expect(field('[data-video-denoise]', HTMLInputElement).closest('details')).toBe(advanced)
  expect(field('[data-video-refine]', HTMLInputElement).closest('details')).toBe(advanced)
  expect(field('[data-video-guidance]', HTMLInputElement).closest('details')).toBe(advanced)
  expect(field('[data-video-negative]', HTMLInputElement).closest('details')).toBe(advanced)
  expect(button('Duplicate for comparison').closest('details')).toBe(advanced)
  expect(details('Model and setup details').open).toBe(false)
})

it.each(['cover', 'visualizer'] as const)('omits ignored generation controls in %s while keeping image, size and seed controls', async mode => {
  await mount({ mode })
  for (const selector of ['[data-video-direction-prompt]', '[data-video-model]', '[data-video-denoise]', '[data-video-refine]', '[data-video-guidance]', '[data-video-negative]']) {
    expect(document.querySelector(selector)).toBeNull()
  }
  expect(field('[data-video-seed]', HTMLInputElement).value).toBe('17')
  expect(field('[data-video-size]', HTMLSelectElement).value).toBe('704')
  expect(field('input[type=file]', HTMLInputElement).accept).toBe('image/png,image/jpeg,image/webp')
  expect(document.body.textContent).toContain('An image is required for this approach')
  expect(document.body.textContent).toContain('Add an image before rendering')
})

it('preserves hidden generation values when the approach changes and restores them on return', async () => {
  const { draft, analyze } = await mount()
  const settings = { ...draft.value.settings }
  button('Animated cover').click(); await flush()
  expect(draft.value.mode).toBe('cover')
  expect(draft.value.direction).toBe('A quiet neon city')
  expect(draft.value.settings).toEqual(settings)
  expect(document.querySelector('[data-video-direction-prompt]')).toBeNull()
  button('Generated scenes').click(); await flush()
  expect(field('[data-video-direction-prompt]', HTMLTextAreaElement).value).toBe('A quiet neon city')
  expect(field('[data-video-negative]', HTMLInputElement).value).toBe('blurry')
  expect(draft.value.settings).toEqual(settings)
  expect(analyze).not.toHaveBeenCalled()
})

it('updates picture dimensions as a supported pair without accepting unknown sizes', async () => {
  const { draft } = await mount()
  const size = field('[data-video-size]', HTMLSelectElement)
  size.value = '768'; size.dispatchEvent(new Event('change', { bubbles: true })); await flush()
  expect(draft.value.settings).toMatchObject({ width: 768, height: 512 })
  size.value = '1280'; size.dispatchEvent(new Event('change', { bubbles: true })); await flush()
  expect(draft.value.settings).toMatchObject({ width: 1280, height: 704 })
  size.value = 'unexpected'; size.dispatchEvent(new Event('change', { bubbles: true })); await flush()
  expect(draft.value.settings).toMatchObject({ width: 1280, height: 704 })
})

it('adds conservative portrait direction without generating or changing timeline settings', async () => {
  const picture = videoProjectFixture(undefined, null)
  const { draft, analyze, project } = await mount({ project: picture })
  const settings = { ...draft.value.settings }
  button('Apply portrait close-up').click(); await flush()
  expect(draft.value.direction).toContain('A quiet neon city')
  expect(draft.value.direction).toContain('restrained facial motion')
  expect(draft.value.settings).toEqual(settings)
  expect(draft.value.shots).toEqual([])
  expect(project.value).toEqual(picture)
  expect(analyze).not.toHaveBeenCalled()
  expect(document.body.textContent).toContain('150–200 pixels')
  expect(document.body.textContent).toContain('does not synchronize lips')
})

it('cannot apply a portrait preset while direction is read-only', async () => {
  const { draft } = await mount({ project: videoProjectFixture(undefined, null), readOnly: true })
  expect(button('Apply portrait close-up').disabled).toBe(true)
  expect(draft.value.direction).toBe('A quiet neon city')
})

it('explains an unavailable comparison model publicly and leaves direction editable', async () => {
  await mount({ readiness: { ...videoReadinessFixture, options: videoReadinessFixture.options.map(option => ({ ...option, available: false, reason: 'model_not_installed' })) } })
  const model = field('[data-video-model]', HTMLSelectElement)
  const comparison = model.querySelector('option[value=ltx25]')
  if (!(comparison instanceof HTMLOptionElement)) throw new Error('Missing LTX-2.5 option')
  expect(comparison.disabled).toBe(true)
  expect(document.body.textContent).toContain('The selected generation model is not installed.')
  expect(field('[data-video-direction-prompt]', HTMLTextAreaElement).disabled).toBe(false)
  expect(button('Animated cover').disabled).toBe(false)
})

it('allows the installed comparison model without starting analysis', async () => {
  const { draft, analyze } = await mount({ readiness: { ...videoReadinessFixture, options: videoReadinessFixture.options.map(option => ({ ...option, available: true, reason: '' })) } })
  const model = field('[data-video-model]', HTMLSelectElement)
  const comparison = model.querySelector('option[value=ltx25]')
  if (!(comparison instanceof HTMLOptionElement)) throw new Error('Missing LTX-2.5 option')
  expect(comparison.disabled).toBe(false)
  model.value = 'ltx25'; model.dispatchEvent(new Event('change', { bubbles: true })); await flush()
  expect(draft.value.settings.engine_pack).toBe('ltx25')
  expect(analyze).not.toHaveBeenCalled()
})

it('keeps disk details and known warnings in setup while sanitizing unknown setup strings', async () => {
  await mount({ readiness: { ...videoReadinessFixture, warnings: ['visual_quality_unverified', '/private/setup/secret'], options: videoReadinessFixture.options.map(option => ({ ...option, available: false, reason: '/private/model/secret', warnings: ['insufficient_disk_space', 'ltx25_comparison_opt_in', '/private/warning/secret'] })) } })
  const setup = details('Model and setup details')
  expect(setup.textContent).toContain('Required:')
  expect(setup.textContent).toContain('not enough free disk space')
  expect(setup.textContent).toContain('experimental comparison option')
  expect(setup.textContent).toContain('Visual quality and GPU memory use require testing')
  expect(setup.textContent).toContain('Additional setup checks need attention')
  expect(document.body.textContent).not.toContain('/private/')
})

it.each([{ gate: 'read-only', readOnly: true }, { gate: 'FFmpeg', readiness: { ...videoReadinessFixture, ffmpeg_ready: false } }, { gate: 'six images', project: { ...videoProjectFixture(), references: Array.from({ length: 6 }, (_, index) => ({ id: String(index), name: `Image ${index}`, bytes: 1, width: 50, height: 50, url: `/image/${index}` })) } }])('blocks image upload at the $gate gate', async options => {
  const { upload } = await mount(options)
  expect(field('input[type=file]', HTMLInputElement).disabled).toBe(true)
  await selectFile(new File(['image'], 'image.png', { type: 'image/png' }))
  expect(upload).not.toHaveBeenCalled()
})

it('emits valid image files once and rejects invalid types or files over 20 MB', async () => {
  const { upload } = await mount()
  const valid = new File(['image'], 'image.webp', { type: 'image/webp' })
  await selectFile(valid)
  expect(upload).toHaveBeenCalledExactlyOnceWith(valid)
  await selectFile(new File(['audio'], 'audio.wav', { type: 'audio/wav' }))
  expect(upload).toHaveBeenCalledTimes(1)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('valid PNG, JPEG or WebP')
  const tooLarge = new File(['large image'], 'big.png', { type: 'image/png' })
  Object.defineProperty(tooLarge, 'size', { value: 20 * 1024 * 1024 + 1 })
  await selectFile(tooLarge)
  expect(upload).toHaveBeenCalledTimes(1)
  expect(document.querySelector('[role=alert]')?.textContent).toContain('20 MB')
})

it('clears an old project upload error when another project is opened', async () => {
  const { project } = await mount()
  await selectFile(new File(['audio'], 'audio.wav', { type: 'audio/wav' }))
  expect(document.querySelector('[role=alert]')?.textContent).toContain('valid PNG, JPEG or WebP')
  project.value = videoProjectFixture('e'.repeat(32)); await flush()
  expect(document.querySelector('[role=alert]')).toBeNull()
})

it('explains source-change analysis blocking while allowing storyboard navigation', async () => {
  const { analyze, advance } = await mount({ project: { ...videoProjectFixture(), source_changed: true }, canAnalyze: false })
  expect(button('Analyze song').disabled).toBe(true)
  expect(document.body.textContent).toContain('Create a new project for the current audio')
  button('Analyze song').click(); button('Edit storyboard').click(); await flush()
  expect(analyze).not.toHaveBeenCalled()
  expect(advance).toHaveBeenCalledTimes(1)
})

it.each([null, { ...videoReadinessFixture, analysis_ready: false }, { ...videoReadinessFixture, ffmpeg_ready: false }])('explains analysis dependency blocking for readiness %j', async readiness => {
  await mount({ readiness, canAnalyze: false })
  expect(button('Analyze song').disabled).toBe(true)
  expect(document.body.textContent).toContain(readiness === null ? 'Could not check installed models' : 'Audio analysis needs NumPy and FFmpeg')
})

it('forwards enabled analysis, storyboard and duplicate actions without changing the draft', async () => {
  const { draft, analyze, advance, duplicate } = await mount()
  const before = JSON.stringify(draft.value)
  button('Analyze song').click(); button('Edit storyboard').click()
  const advanced = details('Advanced options'); advanced.open = true
  button('Duplicate for comparison').click(); await flush()
  expect(analyze).toHaveBeenCalledTimes(1)
  expect(advance).toHaveBeenCalledTimes(1)
  expect(duplicate).toHaveBeenCalledTimes(1)
  expect(JSON.stringify(draft.value)).toBe(before)
})

it('locks editing and duplicate actions during project work but leaves navigation available', async () => {
  const { draft, duplicate, advance } = await mount({ readOnly: true, canAnalyze: false })
  const approach = button('Animated cover')
  expect(approach.disabled).toBe(true)
  approach.click(); await flush()
  expect(draft.value.mode).toBe('generated')
  const fields = [...document.querySelectorAll('input:not([type=file]), select, textarea')]
  expect(fields.length).toBeGreaterThan(5)
  for (const item of fields) {
    expect(item.closest('fieldset')?.disabled).toBe(true)
  }
  expect(button('Duplicate for comparison').disabled).toBe(true)
  button('Duplicate for comparison').click(); button('Edit storyboard').click(); await flush()
  expect(duplicate).not.toHaveBeenCalled()
  expect(advance).toHaveBeenCalledTimes(1)
})

it('says character lock is image conditioning and hides it on a song', async () => {
  await mount()
  expect(document.querySelector('[data-character-lock]')).toBeNull()
  app?.unmount()
  document.body.replaceChildren()
  const project = videoProjectFixture('d'.repeat(32), null)
  project.preset = 'none'
  await mount({ project })
  const box = document.querySelector('[data-character-lock]')
  expect(box).toBeTruthy()
  expect(document.body.textContent).toContain('not a trained video model')
  expect(document.body.textContent).toContain('image conditioning')
})
