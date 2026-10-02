import { describe, expect, it } from 'vitest'
import { decodeSchema } from './schemaValidation'

describe('strict voice media bounds', () => {
  it('rejects zero-length media and accepts a positive duration', () => {
    const schema = { type: 'number', exclusiveMinimum: 0 } as const
    expect(decodeSchema(schema, 0, {})).toBe(false)
    expect(decodeSchema(schema, 0.01, {})).toBe(true)
  })
  it('enforces exclusive upper bounds', () => {
    const schema = { type: 'number', exclusiveMaximum: 1 } as const
    expect(decodeSchema(schema, 1, {})).toBe(false)
    expect(decodeSchema(schema, 0.99, {})).toBe(true)
  })
})
