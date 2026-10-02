// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { formatClock, formatCreated } from './formatCreated'
import { friendlyTitle } from '../utils/trackTitle'
import { setLocale } from '../i18n'
afterEach(() => { vi.useRealTimers(); setLocale('en') })
it.each([[null, '—'], [undefined, '—'], [NaN, '—'], [Infinity, '—'], [-1, '—'], [0, '0:00'], [60, '1:00'], [3601, '1:00:01']] as const)('formats duration %s as %s', (seconds, expected) => { expect(formatClock(seconds)).toBe(expected) })
it.each([NaN, Infinity, -Infinity, 9e99])('keeps invalid dates readable for %s', timestamp => { expect(formatCreated(timestamp)).toEqual({ label: '—', full: '—' }) })
it('uses localized relative calendar dates and keeps the exact tooltip', () => {
 vi.useFakeTimers(); vi.setSystemTime(new Date(2026, 9, 1, 12)); setLocale('en')
 expect(formatCreated(new Date(2026, 8, 30, 23).getTime()).label).toContain('Yesterday')
 setLocale('en'); expect(formatCreated(Date.now()).label).toContain('Today')
 expect(formatCreated(new Date(2025, 9, 1).getTime()).label).toContain('2025')
})
it('keeps full short titles and case, and truncates long single phrases without storing a new title', () => {
 expect(friendlyTitle('iPhone song')).toBe('iPhone song')
 expect(friendlyTitle('  iPhone   song  ')).toBe('iPhone song')
 const long = 'abcdefghij'.repeat(20)
 expect(friendlyTitle(long)).toHaveLength(80); expect(friendlyTitle(long).endsWith('…')).toBe(true)
 expect(long).toHaveLength(200)
})
