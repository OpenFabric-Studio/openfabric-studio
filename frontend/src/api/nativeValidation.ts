export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error('Invalid model response')
  return value
}

export function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Invalid model response')
  return value
}

export function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('Invalid model response')
  return value
}

export function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Invalid model response')
  return value
}

export function nullableString(value: unknown): string | null {
  return value == null ? null : string(value)
}

export function nullableNumber(value: unknown): number | null {
  return value == null ? null : number(value)
}

export function literal<const T extends string>(value: unknown, allowed: readonly T[]): T {
  for (const candidate of allowed) if (candidate === value) return candidate
  throw new Error('Invalid model response')
}

export function array<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error('Invalid model response')
  return value.map(parse)
}

export function stringArray(value: unknown): string[] {
  return array(value, string)
}

export function nativePayload(value: unknown): unknown {
  if (isRecord(value) && 'data' in value && typeof value.code === 'number') return value.data
  return value
}
