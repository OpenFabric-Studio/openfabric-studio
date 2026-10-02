export function formatClock(seconds: number | null | undefined): string {
 if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'
 const rounded = Math.round(seconds)
 const hours = Math.floor(rounded / 3600), minutes = Math.floor(rounded % 3600 / 60), rest = String(rounded % 60).padStart(2, '0')
 return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`
}
/** Calendar dates, rather than 24-hour intervals, remain correct at daylight-saving changes. */
export function formatCreated(timestamp: number): { label: string; full: string } {
 if (!Number.isFinite(timestamp)) return { label: '—', full: '—' }
 const when = new Date(timestamp), now = new Date()
 if (!Number.isFinite(when.getTime())) return { label: '—', full: '—' }
 const locale = 'en-US'
 const ordinal = (date: Date) => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000
 const difference = ordinal(when) - ordinal(now)
 const day = difference === 0 || difference === -1
  ? new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(difference, 'day')
  : when.toLocaleDateString(locale, { day: 'numeric', month: 'short', ...(when.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }) })
 const label = day.charAt(0).toUpperCase() + day.slice(1)
 const time = when.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
 return { label: `${label}, ${time}`, full: when.toLocaleString(locale) }
}
