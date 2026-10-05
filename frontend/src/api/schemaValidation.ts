/** Runtime support for the generated Pydantic JSON Schema contracts. */
interface Schema {
  readonly $ref?: string
  readonly type?: string
  readonly anyOf?: readonly Schema[]
  readonly oneOf?: readonly Schema[]
  readonly discriminator?: { readonly propertyName: string; readonly mapping?: Readonly<Record<string, string>> }
  readonly enum?: readonly unknown[]
  readonly const?: unknown
  readonly properties?: Readonly<Record<string, Schema>>
  readonly required?: readonly string[]
  readonly additionalProperties?: boolean | Schema
  readonly items?: Schema
  readonly minimum?: number
  readonly maximum?: number
  readonly exclusiveMinimum?: number
  readonly exclusiveMaximum?: number
  readonly minLength?: number
  readonly maxLength?: number
  readonly minItems?: number
  readonly maxItems?: number
  readonly pattern?: string
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function decodeSchema(schema: Schema, value: unknown, definitions: Readonly<Record<string, Schema>>): boolean {
  if (schema.$ref) {
    const referenced = definitions[schema.$ref.split('/').at(-1) ?? '']
    return referenced !== undefined && decodeSchema(referenced, value, definitions)
  }
  if (schema.anyOf) return schema.anyOf.some((branch) => decodeSchema(branch, value, definitions))
  if (schema.discriminator) {
    if (!isObject(value) || !Object.hasOwn(value, schema.discriminator.propertyName)) return false
    const tag = value[schema.discriminator.propertyName]
    if (typeof tag !== 'string' || (schema.discriminator.mapping && !Object.hasOwn(schema.discriminator.mapping, tag))) return false
  }
  if (schema.oneOf) return schema.oneOf.filter((branch) => decodeSchema(branch, value, definitions)).length === 1
  if (schema.enum) return schema.enum.includes(value)
  if ('const' in schema) return schema.const === value
  switch (schema.type) {
    case 'null': return value === null
    case 'boolean': return typeof value === 'boolean'
    case 'string': return typeof value === 'string' && value.length >= (schema.minLength ?? 0) && value.length <= (schema.maxLength ?? Infinity) && (schema.pattern === undefined || new RegExp(schema.pattern).test(value))
    case 'integer':
    case 'number': return typeof value === 'number' && Number.isFinite(value) && (schema.type !== 'integer' || Number.isInteger(value)) && value >= (schema.minimum ?? -Infinity) && value <= (schema.maximum ?? Infinity) && (schema.exclusiveMinimum === undefined || value > schema.exclusiveMinimum) && (schema.exclusiveMaximum === undefined || value < schema.exclusiveMaximum)
    case 'array': return Array.isArray(value) && value.length >= (schema.minItems ?? 0) && value.length <= (schema.maxItems ?? Infinity) && schema.items !== undefined && value.every((item: unknown) => decodeSchema(schema.items ?? {}, item, definitions))
    case 'object': {
      if (!isObject(value)) return false
      const properties = schema.properties ?? {}
      if (schema.required?.some((key) => !Object.hasOwn(value, key))) return false
      for (const [key, item] of Object.entries(value)) {
        const field = Object.hasOwn(properties, key) ? properties[key] : undefined
        if (field) {
          if (!decodeSchema(field, item, definitions)) return false
        } else if (schema.additionalProperties === false) return false
        else if (typeof schema.additionalProperties === 'object' && !decodeSchema(schema.additionalProperties, item, definitions)) return false
      }
      return true
    }
    default: return false
  }
}
