import { createI18n } from 'vue-i18n'
import en from '../locales/en'

export type LocaleCode = 'en'
const STORAGE_KEY = 'openfabric_locale'

export const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: { en },
})

document.documentElement.lang = 'en'

export function setLocale(locale: LocaleCode) {
  i18n.global.locale.value = locale
  document.documentElement.lang = locale
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // ignore - just won't persist across reloads
  }
}

export function currentLocale(): LocaleCode {
  return 'en'
}
