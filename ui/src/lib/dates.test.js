import { describe, it, expect } from 'vitest'
import { formatDate, formatMonth, formatDateRange } from './dates.js'

describe('formatMonth', () => {
  it('renders a YYYY-MM bucket as "MMM YYYY"', () => {
    expect(formatMonth('2026-05')).toBe('May 2026')
  })
  it('is empty for a missing value', () => {
    expect(formatMonth('')).toBe('')
    expect(formatMonth(null)).toBe('')
  })
})

describe('formatDate', () => {
  it('drops the year for the current year', () => {
    const thisYear = new Date().getFullYear()
    expect(formatDate(`${thisYear}-06-05`)).toBe('05 Jun')
  })
  it('keeps the year for other years', () => {
    expect(formatDate('2019-06-05')).toBe('05 Jun 2019')
  })
})

describe('formatDateRange', () => {
  it('collapses same-month ranges', () => {
    expect(formatDateRange('2026-06-03', '2026-06-21')).toBe('03 - 21 Jun 2026')
  })
  it('spans months within a year', () => {
    expect(formatDateRange('2026-06-28', '2026-07-04')).toBe('28 Jun - 04 Jul 2026')
  })
  it('spans years by month', () => {
    expect(formatDateRange('2025-12-20', '2026-02-10')).toBe('Dec 2025 - Feb 2026')
  })
})
