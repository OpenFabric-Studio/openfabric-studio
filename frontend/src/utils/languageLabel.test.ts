import { languageLabel } from './languageLabel'
import { afterEach, expect, it, vi } from 'vitest'

it('keeps native names and adds a translated label in the selected UI language', () => {
  expect(languageLabel('ru', 'en', 'Русский')).toBe('Русский · Russian')
  expect(languageLabel('en', 'en', 'English')).toBe('English')
  expect(languageLabel('ja', 'en', '日本語')).toBe('日本語 · Japanese')
})

it('preserves the native fallback on unsupported or invalid language identifiers', () => {
  expect(languageLabel('not_a_language', 'en', 'Native')).toBe('Native')
})

it('falls back to the native name when Intl.DisplayNames is unavailable', () => {
  vi.stubGlobal('Intl', { ...Intl, DisplayNames: undefined })
  expect(languageLabel('ru', 'en', 'Русский')).toBe('Русский')
  vi.unstubAllGlobals()
})

afterEach(() => { vi.unstubAllGlobals() })
