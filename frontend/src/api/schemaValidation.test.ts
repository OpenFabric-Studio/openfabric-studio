import { describe, expect, it } from 'vitest'
import { decodeSchema } from './schemaValidation'

const definitions = {
  Local: { type: 'object', required: ['provider'], additionalProperties: false,
    properties: { provider: { const: 'local' } } },
  Cloud: { type: 'object', required: ['provider', 'model'], additionalProperties: false,
    properties: { provider: { const: 'openrouter' }, model: { type: 'string', minLength: 1 } } },
} as const
const schema = { oneOf: [{ $ref: '#/$defs/Local' }, { $ref: '#/$defs/Cloud' }] } as const

describe('exclusive schema unions', () => {
  it('validates each complete discriminated variant', () => {
    expect(decodeSchema(schema, { provider: 'local' }, definitions)).toBe(true)
    expect(decodeSchema(schema, { provider: 'openrouter', model: 'vendor/model' }, definitions)).toBe(true)
  })
  it.each([{}, { provider: 'unknown' }, { provider: 'openrouter' },
    { provider: 'local', model: 'vendor/model' }, { provider: 'openrouter', model: '' }])(
    'rejects invalid or mixed variants', (value) => expect(decodeSchema(schema, value, definitions)).toBe(false),
  )
  it('requires exactly one matching branch', () => {
    const overlapping = { oneOf: [{ type: 'number' }, { type: 'integer' }] } as const
    expect(decodeSchema(overlapping, 3, {})).toBe(false)
    expect(decodeSchema(overlapping, 3.5, {})).toBe(true)
    expect(decodeSchema(overlapping, Infinity, {})).toBe(false)
  })
  it('supports nested nullable unions without weakening variants', () => {
    const nullable = { anyOf: [schema, { type: 'null' }] } as const
    expect(decodeSchema(nullable, null, definitions)).toBe(true)
    expect(decodeSchema(nullable, { provider: 'openrouter' }, definitions)).toBe(false)
  })
})

it('requires the discriminator even when member schemas default their tags', () => {
  const defaulted = {
    Local: { type: 'object', additionalProperties: false, properties: { provider: { const: 'local', default: 'local' } } },
    Cloud: { type: 'object', required: ['model'], additionalProperties: false, properties: { provider: { const: 'openrouter', default: 'openrouter' }, model: { type: 'string' } } },
  } as const
  const tagged = { ...schema, discriminator: { propertyName: 'provider', mapping: { local: '#/$defs/Local', openrouter: '#/$defs/Cloud' } } } as const
  expect(decodeSchema(tagged, { model: 'vendor/model' }, defaulted)).toBe(false)
  expect(decodeSchema(tagged, { provider: 'openrouter', model: 'vendor/model' }, defaulted)).toBe(true)
})
