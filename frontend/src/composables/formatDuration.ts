import { i18n } from '../i18n'

// Shared "M:SS (Nс)" formatter for a track's total length, used anywhere a
// finished track's duration is shown (as opposed to WaveformPlayer's own
// running playhead label, which stays in plain M:SS).
export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds) || totalSeconds < 0) return '—'
  const rounded = Math.round(totalSeconds)
  const m = Math.floor(rounded / 60)
  const s = rounded % 60
  return `${m}:${String(s).padStart(2, '0')} (${rounded}${i18n.global.t('common.secondsUnit')})`
}
