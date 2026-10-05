// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { useLoraRegistry } from './useLoraRegistry'

const key = 'aicollector_ace_loras_v1'
let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); localStorage.clear() })

function mountRegistry() {
  let registry: ReturnType<typeof useLoraRegistry> | undefined
  app = createApp({ setup() { registry = useLoraRegistry(); return () => null } })
  app.mount(document.body.appendChild(document.createElement('div')))
  if (!registry) throw new Error('Missing registry')
  return registry
}

it.each(['null', '{}', '[null]', '[{"name":2,"path":3}]', '[{"name":"Voice"}]', '[{"name":"","path":"adapter"}]', '[{"name":"Voice","path":""}]', '{broken'])('rejects corrupt stored registry %s', stored => {
  localStorage.setItem(key, stored)
  const registry = mountRegistry()
  expect(registry.loras.value).toEqual([])
  registry.add('Usable voice', '/adapter')
  expect(registry.loras.value).toEqual([{ name: 'Usable voice', path: '/adapter' }])
})

it('preserves valid adapters and round trips additions, duplicate paths and removals', async () => {
  localStorage.setItem(key, JSON.stringify([{ name: 'Existing voice', path: '/existing' }]))
  const registry = mountRegistry()
  registry.add(' New voice ', ' /new ')
  registry.add('Duplicate name', '/new')
  await nextTick()
  expect(JSON.parse(localStorage.getItem(key) ?? 'null')).toEqual([{ name: 'Existing voice', path: '/existing' }, { name: 'New voice', path: '/new' }])
  registry.remove('/existing'); await nextTick()
  expect(JSON.parse(localStorage.getItem(key) ?? 'null')).toEqual([{ name: 'New voice', path: '/new' }])
})

it('keeps names within the backend generation snapshot label limit', () => {
  localStorage.setItem(key, JSON.stringify([{ name: 'x'.repeat(501), path: '/adapter' }]))
  const registry = mountRegistry()
  expect(registry.loras.value).toEqual([])
  registry.add('x'.repeat(501), '/adapter')
  expect(registry.loras.value).toEqual([])
  registry.add('x'.repeat(500), '/adapter')
  expect(registry.loras.value).toHaveLength(1)
})
