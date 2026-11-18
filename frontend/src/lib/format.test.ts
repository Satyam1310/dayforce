import { describe, expect, it } from 'vitest'
import { formatDate, formatTime, formatValue, humanize, isIsoDateTime, shortId } from './format'

describe('format helpers', () => {
  it('shows UTC times in IST (UTC+5:30)', () => {
    expect(formatTime('2026-03-10T03:32:00+00:00')).toBe('09:02')
    expect(formatTime('2026-03-10T12:30:00+00:00')).toBe('18:00')
  })

  it('handles the day boundary in Asia/Kolkata', () => {
    // 18:30 UTC on the 11th is already 00:00 on the 12th in India
    expect(formatTime('2026-03-11T18:30:00+00:00')).toBe('00:00')
  })

  it('formats a calendar day', () => {
    expect(formatDate('2026-03-10')).toContain('10 Mar 2026')
  })

  it('detects ISO date-times', () => {
    expect(isIsoDateTime('2026-03-10T12:30:00+00:00')).toBe(true)
    expect(isIsoDateTime('2026-03-10')).toBe(false)
    expect(isIsoDateTime('30')).toBe(false)
    expect(isIsoDateTime(null)).toBe(false)
  })

  it('formats evidence values', () => {
    expect(formatValue(null)).toBe('—')
    expect(formatValue(8.9712)).toBe('8.97')
    expect(formatValue('MISSING_BREAK')).toBe('MISSING_BREAK')
    expect(formatValue('2026-03-10T12:30:00+00:00')).toContain('18:00 IST')
  })

  it('humanizes codes and shortens ids', () => {
    expect(humanize('MISSED_PUNCH')).toBe('Missed punch')
    expect(humanize('scheduled_shift_end')).toBe('Scheduled shift end')
    expect(shortId('22222222-2222-4222-8222-000000000001')).toBe('…0001')
  })
})
