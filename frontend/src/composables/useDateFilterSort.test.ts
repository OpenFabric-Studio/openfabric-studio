import { afterEach, expect, it, vi } from 'vitest'
import { useDateFilterSort, type DatePreset } from './useDateFilterSort'

afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs() })

it.each<{ zone: string; now: string; preset: DatePreset; from: string; to: string }>([
  { zone: 'Atlantic/Canary', now: '2026-10-12T00:15:00+01:00', preset: 'today', from: '2026-10-12', to: '2026-10-12' },
  { zone: 'America/Los_Angeles', now: '2026-10-11T23:45:00-07:00', preset: 'today', from: '2026-10-11', to: '2026-10-11' },
  { zone: 'Asia/Tokyo', now: '2027-01-02T00:15:00+09:00', preset: '7d', from: '2026-12-27', to: '2027-01-02' },
  { zone: 'Asia/Tokyo', now: '2028-03-01T00:15:00+09:00', preset: '30d', from: '2028-02-01', to: '2028-03-01' },
  { zone: 'America/New_York', now: '2026-03-09T00:15:00-04:00', preset: '7d', from: '2026-03-03', to: '2026-03-09' },
  { zone: 'America/New_York', now: '2026-11-02T23:45:00-05:00', preset: '7d', from: '2026-10-27', to: '2026-11-02' },
])('uses inclusive local calendar days for $preset in $zone at $now', ({ zone, now, preset, from, to }) => {
  vi.stubEnv('TZ', zone)
  vi.useFakeTimers()
  vi.setSystemTime(new Date(now))
  const start = new Date(`${from}T00:00:00`).getTime()
  const end = new Date(`${to}T23:59:59.999`).getTime()
  const items = [
    { id: 'before', createdAt: start - 1 }, { id: 'first', createdAt: start },
    { id: 'last', createdAt: end }, { id: 'after', createdAt: end + 1 },
  ]
  const filter = useDateFilterSort(() => items)
  filter.applyPreset(preset)
  expect(filter.dateFrom.value).toBe(from)
  expect(filter.dateTo.value).toBe(to)
  expect(filter.filteredSorted.value.map(item => item.id)).toEqual(['last', 'first'])
  filter.sortOrder.value = 'oldest'
  expect(filter.filteredSorted.value.map(item => item.id)).toEqual(['first', 'last'])
  expect(items.map(item => item.id)).toEqual(['before', 'first', 'last', 'after'])
})

it('clears both boundaries with All and restores default sorting on reset', () => {
  const filter = useDateFilterSort(() => [{ createdAt: 1 }, { createdAt: 2 }])
  filter.applyPreset('today')
  filter.applyPreset('all')
  expect(filter.isFiltered.value).toBe(false)
  expect(filter.activePreset.value).toBe('all')
  expect(filter.filteredSorted.value.map(item => item.createdAt)).toEqual([2, 1])
  filter.sortOrder.value = 'oldest'
  filter.reset()
  expect(filter.activePreset.value).toBeNull()
  expect(filter.sortOrder.value).toBe('newest')
})
