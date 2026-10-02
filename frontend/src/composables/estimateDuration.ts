// Two verses and two choruses of a usual length land on 2:00, the length the
// lyrics help calls enough for that shape. Extra lines add 4 seconds. With no
// lyric sheet, a full song is 3:00. A set BPM scales from 120.

const EXTRA_LINE_SEC = 4
const UNTAGGED_PAD_SEC = 24
const MIN_SEC = 30
const SLIDER_MAX_SEC = 300
const SUGGEST_MAX_SEC = 600
const FULL_SONG_SEC = 180

const SECTION: Record<string, { base: number; typical: number }> = {
  intro: { base: 12, typical: 0 },
  verse: { base: 32, typical: 8 },
  prechorus: { base: 12, typical: 2 },
  chorus: { base: 28, typical: 4 },
  hook: { base: 12, typical: 2 },
  bridge: { base: 20, typical: 4 },
  break: { base: 16, typical: 0 },
  outro: { base: 16, typical: 2 },
  part: { base: 24, typical: 4 },
}

export interface DurationFit {
  /** Slider stop, kept inside 30–300 seconds. */
  slider: number
  /** Fitting length, kept inside 30–600 seconds. */
  suggested: number
  kind: 'lyrics' | 'full' | 'instrumental'
}

function kindOf(header: string): keyof typeof SECTION {
  const text = header.toLowerCase().replace(/[_/]+/g, ' ').replace(/\d+/g, ' ')
  const compact = text.replace(/[\s-]+/g, '')
  if (compact.includes('prechorus') || text.includes('предприпев')) return 'prechorus'
  if (text.includes('chorus') || text.includes('припев')) return 'chorus'
  if (text.includes('verse') || text.includes('куплет')) return 'verse'
  if (text.includes('bridge') || text.includes('бридж')) return 'bridge'
  if (text.includes('intro') || text.includes('интро')) return 'intro'
  if (text.includes('outro') || text.includes('ending') || text.includes('аутро') || text.includes('концовк')) return 'outro'
  if (text.includes('hook') || text.includes('хук')) return 'hook'
  if (
    text.includes('interlude') || text.includes('break') || text.includes('instrumental') || text.includes('solo')
    || text.includes('проигрыш') || text.includes('интерлюд') || text.includes('соло')
  ) return 'break'
  return 'part'
}

function sectionSeconds(kind: keyof typeof SECTION, lines: number): number {
  const spec = SECTION[kind]
  return spec.base + Math.max(0, lines - spec.typical) * EXTRA_LINE_SEC
}

function lyricSeconds(lyrics: string): number | null {
  let sawHeader = false
  let current: { kind: keyof typeof SECTION; lines: number } | null = null
  const sections: { kind: keyof typeof SECTION; lines: number }[] = []
  let loose = 0
  for (const row of lyrics.split(/\r?\n/)) {
    const trimmed = row.trim()
    if (!trimmed) continue
    const header = trimmed.match(/^\[([^\]]+)\]$/)
    if (header) {
      sawHeader = true
      if (current) sections.push(current)
      current = { kind: kindOf(header[1]), lines: 0 }
      continue
    }
    if (/^\([^)]*\)$/.test(trimmed)) continue
    if (current) current.lines += 1
    else loose += 1
  }
  if (current) sections.push(current)
  if (!sawHeader) {
    if (loose === 0) return null
    return UNTAGGED_PAD_SEC + loose * EXTRA_LINE_SEC
  }
  let total = loose > 0 ? sectionSeconds('part', loose) : 0
  for (const section of sections) total += sectionSeconds(section.kind, section.lines)
  return total
}

function snap(seconds: number, max: number): number {
  const stepped = Math.round(seconds / 5) * 5
  return Math.min(max, Math.max(MIN_SEC, stepped))
}

function scaled(seconds: number, kind: DurationFit['kind'], bpm?: number | null): DurationFit {
  let length = seconds
  if (bpm != null && Number.isFinite(bpm) && bpm >= 40 && bpm <= 240) length = seconds * (120 / bpm)
  return { slider: snap(length, SLIDER_MAX_SEC), suggested: snap(length, SUGGEST_MAX_SEC), kind }
}

export function estimateTrackDuration(input: {
  lyrics?: string
  instrumental?: boolean
  bpm?: number | null
}): DurationFit {
  if (input.instrumental) return scaled(FULL_SONG_SEC, 'instrumental', input.bpm)
  const fromLyrics = lyricSeconds(input.lyrics || '')
  if (fromLyrics == null) return scaled(FULL_SONG_SEC, 'full', input.bpm)
  return scaled(fromLyrics, 'lyrics', input.bpm)
}
