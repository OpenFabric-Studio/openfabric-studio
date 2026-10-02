/** Shorten presentation only; keep the user's case and never change the stored/editable title. */
export function friendlyTitle(title: string): string {
 const clean = title.replace(/\s+/g, ' ').trim()
 if (clean.length <= 80) return clean
 const phrases = clean.split(',').map(phrase => phrase.trim()).filter(Boolean)
 let result = phrases[0] || clean
 for (const phrase of phrases.slice(1)) { if (result.length + phrase.length + 2 > 79) break; result += ', ' + phrase }
 if (result.length > 79) { const cut = result.slice(0, 79); const space = cut.lastIndexOf(' '); result = space > 0 ? cut.slice(0, space) : cut }
 return result + '…'
}
