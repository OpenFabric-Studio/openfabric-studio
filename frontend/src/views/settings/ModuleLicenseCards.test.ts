// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, h, type App } from 'vue'
import { i18n } from '../../i18n'
import type { ModuleLicenseDeclaration } from '../../api/generated'
import ModuleLicenseCards from './ModuleLicenseCards.vue'

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren() })
function mount(licenses: ModuleLicenseDeclaration[]) {
  const node = document.body.appendChild(document.createElement('div'))
  app = createApp({ render: () => h(ModuleLicenseCards, { licenses }) }).use(i18n); app.mount(node); return node
}
const unknown: ModuleLicenseDeclaration = { component_id: 'samples', name: 'Local samples', scope: 'samples', status: 'unknown', notes: 'Check the original source.', reviewed_at: '2026-10-17' }

it('keeps unknown sample and dataset permissions explicit', () => {
  const node = mount([unknown, { ...unknown, component_id: 'dataset', scope: 'dataset', name: 'Training dataset' }])
  expect(node.textContent).toContain('Samples'); expect(node.textContent).toContain('Dataset')
  expect(node.textContent).toContain('Restriction unknown'); expect(node.textContent).toContain('Redistribution unknown')
  expect(node.textContent).toContain('Copyleft unknown')
  expect(node.textContent).toContain('do not certify commercial permission')
})

it('separates publisher restrictions from public evidence and expected artifact identity', () => {
  const node = mount([{ ...unknown, component_id: 'weights', name: 'Model weights', scope: 'model', status: 'declared', declared_license: 'Publisher terms', restriction: 'noncommercial', redistribution: 'forbidden', copyleft: 'none_declared', source_url: 'https://example.org/terms', evidence: [{ kind: 'publisher_terms', source_url: 'https://example.org/terms/v2', observed_at: '2026-10-17', reviewer: 'OpenFabric Studio', revision: 'v2' }], identities: [{ repository_url: 'https://example.org/model', revision: 'v2', filename: 'model.safetensors', sha256: 'a'.repeat(64) }] }])
  expect(node.textContent).toContain('Non-commercial'); expect(node.textContent).toContain('Redistribution forbidden')
  expect(node.textContent).toContain('Expected component identity'); expect(node.textContent).toContain('model.safetensors')
  expect(node.textContent).toContain('Publisher terms observed'); expect(node.textContent).not.toContain('Verified licence')
  expect([...node.querySelectorAll('a')].map(link => link.getAttribute('href'))).toContain('https://example.org/terms/v2')
})

it('does not expose unsafe declaration, evidence or repository links', () => {
  const node = mount([{ ...unknown, source_url: 'javascript:alert(1)', evidence: [{ kind: 'publisher_terms', source_url: 'https://user:secret@example.org/terms', observed_at: '2026-10-17', reviewer: 'Public record' }], identities: [{ repository_url: 'http://example.org/model', filename: 'weights.bin' }] }])
  expect(node.querySelectorAll('a')).toHaveLength(0)
})

it('makes a whole-bank redistribution restriction explicit', () => {
  const node = mount([{ ...unknown, redistribution: 'bank_only' }])
  expect(node.textContent).toContain('Whole bank only')
  expect(node.textContent).toContain('individual sample extraction is not cleared')
})
