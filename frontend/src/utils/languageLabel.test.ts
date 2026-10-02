import { afterEach, expect, it, vi } from 'vitest'
import { languageLabel } from './languageLabel'
afterEach(() => vi.restoreAllMocks())
it('keeps native names and adds a translated label in the selected UI language', () => {
  expect(languageLabel('ru', 'en', 'Русский')).toBe('Русский · Russian')
  expect(languageLabel('en', 'ru', 'English')).toBe('English · английский')
  expect(languageLabel('ru', 'ru', 'Русский')).toBe('Русский')
  expect(languageLabel('en', 'en', 'English')).toBe('English')
})
it('preserves the native fallback on unsupported or invalid language identifiers', () => {
  expect(languageLabel('not_a_language', 'en', 'Native')).toBe('Native')
  vi.spyOn(Intl, 'DisplayNames').mockImplementation(() => { throw new Error('unsupported') })
  expect(languageLabel('ru', 'en', 'Русский')).toBe('Русский')
})
