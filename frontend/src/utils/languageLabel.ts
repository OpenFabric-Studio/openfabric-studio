/** Preserve autonyms when ICU data or language display names are unavailable. */
export function languageLabel(code: string, uiLocale: string, nativeName: string): string {
  if (typeof Intl.DisplayNames !== 'function') return nativeName
  try {
    const translated = new Intl.DisplayNames([uiLocale], { type: 'language', fallback: 'none' }).of(code)
    return translated && translated.toLowerCase() !== nativeName.toLowerCase() ? `${nativeName} · ${translated}` : nativeName
  } catch { return nativeName }
}
