// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { i18n } from '../../i18n'
import { parseAlignmentResult } from '../../api/generated'
import type { AlignmentEditRequest } from '../../api/generated'
import { replacementPlan } from '../../stores/replacementTestFixtures'
import ReplacementAlignmentEditor from './ReplacementAlignmentEditor.vue'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
function alignment() { const request = replacementPlan().request; if (request.mode !== 'prepared') throw new Error('Missing prepared fixture'); return parseAlignmentResult({ id: 'f'.repeat(32), request: { reference: request.source.source, reference_sha256: request.source.audio_sha256, prepared: request.prepared.source, prepared_sha256: request.prepared.audio_sha256, revision: 1 }, status: 'needs_review', anchors: [{ source_sec: 0, target_sec: 0 }, { source_sec: 10, target_sec: 10 }], phrases: [{ phrase_id: '1'.repeat(32), reference_start_sec: 0, reference_end_sec: 2, prepared_start_sec: 0, prepared_end_sec: 2, reason: 'aligned' }], feature_method: 'synthetic-v1', matcher_method: 'fixture-v1', configuration_sha256: '2'.repeat(64), created_at: '2026-10-17T10:00:00Z' }) }
it('edits a copied anchor with keyboard control and records explicit phrase review', async () => {
  const saved = alignment(), edits: unknown[] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp(ReplacementAlignmentEditor, { alignment: saved, reviewed: [], onSave: (value: Omit<AlignmentEditRequest, 'expected_revision'>) => edits.push(value) }).use(i18n); app.mount(node); await nextTick()
  const anchor = node.querySelector('circle'); anchor?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await nextTick()
  expect(saved.anchors?.[0]?.source_sec).toBe(0)
  node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await nextTick()
  anchor?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); await nextTick()
  expect(edits).toHaveLength(1); expect(edits[0]).toMatchObject({ alignment: { anchors: [{ source_sec: .01, target_sec: 0 }, { source_sec: 10, target_sec: 10 }] }, reviewed_phrase_ids: ['1'.repeat(32)] })
  expect(node.textContent).toContain('Confidence is unknown')
})
it('refuses unsafe slopes and Escape restores the saved proposal', async () => {
  const edits: unknown[] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp(ReplacementAlignmentEditor, { alignment: alignment(), reviewed: [], onSave: (value: Omit<AlignmentEditRequest, 'expected_revision'>) => edits.push(value) }).use(i18n); app.mount(node); await nextTick()
  const fields = node.querySelectorAll<HTMLInputElement>('input[type="number"]'), target = fields[3]
  if (!target) throw new Error('Missing anchor field')
  target.value = '30'; target.dispatchEvent(new Event('input')); await nextTick()
  const save = [...node.querySelectorAll('button')].find(button => button.textContent === 'Save reviewed alignment'); save?.click(); await nextTick()
  expect(node.querySelector('[role="alert"]')?.textContent).toContain('increasing anchors'); expect(edits).toHaveLength(0)
  node.querySelector('circle')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await nextTick()
  expect(node.querySelectorAll<HTMLInputElement>('input[type="number"]')[3]?.value).toBe('10')
})

it('preserves unsaved anchors and review selections on an unchanged server refresh', async () => {
  const current = ref(alignment()), node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(ReplacementAlignmentEditor, { alignment: current.value, reviewed: [] }) }).use(i18n); app.mount(node); await nextTick()
  const field = node.querySelector<HTMLInputElement>('input[type="number"]'); if (!field) throw new Error('Missing anchor')
  field.value = '.1'; field.dispatchEvent(new Event('input')); node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await nextTick()
  current.value = alignment(); await nextTick(); await nextTick()
  expect(Number(node.querySelector<HTMLInputElement>('input[type="number"]')?.value)).toBe(.1)
  expect(node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(true)
})
it('keeps local anchors and phrase reviews on a remote alignment change and blocks saving', async () => {
  const current = ref(alignment()), saves: unknown[] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(ReplacementAlignmentEditor, { alignment: current.value, reviewed: [], onSave: (value: unknown) => saves.push(value) }) }).use(i18n); app.mount(node); await nextTick()
  const field = node.querySelector<HTMLInputElement>('input[type="number"]'); if (!field) throw new Error('Missing anchor')
  field.value = '.1'; field.dispatchEvent(new Event('input')); node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await nextTick()
  current.value = { ...alignment(), anchors: [{ source_sec: 0, target_sec: 0 }, { source_sec: 9, target_sec: 10 }] }; await nextTick(); await nextTick()
  expect(Number(field.value)).toBe(.1); expect(node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(true)
  const save = [...node.querySelectorAll('button')].find(button => button.textContent === 'Save reviewed alignment')
  expect(save?.disabled).toBe(true); save?.click(); expect(saves).toHaveLength(0)
})
it('rebinds an explicitly reviewed draft to the latest alignment identity and clears changed phrase acknowledgements', async () => {
  const current = ref(alignment()), editor = ref<InstanceType<typeof ReplacementAlignmentEditor> | null>(null), saves: Omit<AlignmentEditRequest, 'expected_revision'>[] = [], node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(ReplacementAlignmentEditor, { ref: editor, alignment: current.value, reviewed: [], onSave: value => saves.push(value) }) }).use(i18n); app.mount(node); await nextTick()
  const field = node.querySelector<HTMLInputElement>('input[type="number"]'); if (!field) throw new Error('Missing anchor')
  field.value = '.1'; field.dispatchEvent(new Event('input')); node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(); await nextTick()
  current.value = { ...alignment(), request: { ...alignment().request, revision: 2 }, phrases: alignment().phrases?.map(phrase => ({ ...phrase, reason: 'ambiguous' })) }; await nextTick(); await nextTick()
  editor.value?.reviewRevision(); await nextTick()
  expect(Number(field.value)).toBe(.1); expect(node.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(false)
  const save = [...node.querySelectorAll('button')].find(button => button.textContent === 'Save reviewed alignment'); save?.click(); await nextTick()
  expect(saves[0]?.alignment.request.revision).toBe(2); expect(saves[0]?.alignment.phrases?.[0]?.reason).toBe('ambiguous'); expect(saves[0]?.reviewed_phrase_ids).toEqual([])
})
