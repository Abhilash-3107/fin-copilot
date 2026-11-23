import { describe, it, expect } from 'vitest'
import { coverageLabel, isInProgress, comparisonLabel } from './period.js'

const complete = { month: '2026-10', start: '2026-10-01', end: '2026-10-31', covered_through: '2026-10-31', partial: false }
const partial = { month: '2026-11', start: '2026-11-01', end: '2026-11-30', covered_through: '2026-11-15', partial: true }

describe('coverageLabel', () => {
  it('names the covered days of a partial month while it is still running', () => {
    expect(coverageLabel(partial, '2026-11-15')).toBe('1-15 Nov, in progress')
    expect(coverageLabel(partial, '2026-11-30')).toBe('1-15 Nov, in progress')
  })
  it('drops "in progress" once the month has ended but keeps the covered days', () => {
    const ended = { ...partial, covered_through: '2026-11-28' }
    expect(coverageLabel(ended, '2026-12-01')).toBe('1-28 Nov')
    expect(isInProgress(ended, '2026-12-01')).toBe(false)
  })
  it('names a single covered day as one day, not "1-1"', () => {
    const day = { ...partial, covered_through: '2026-11-01' }
    expect(coverageLabel(day, '2026-11-02')).toBe('1 Nov, in progress')
  })
  it('is null for a complete month or no period', () => {
    expect(coverageLabel(complete, '2026-11-15')).toBeNull()
    expect(coverageLabel(undefined)).toBeNull()
    expect(isInProgress(complete, '2026-10-31')).toBe(false)
  })
})

describe('comparisonLabel', () => {
  it('names a whole previous month by its month', () => {
    expect(comparisonLabel({ month: '2026-09', start: '2026-09-01', end: '2026-09-30' })).toBe('September')
  })
  it('names the same days of the previous month as a range', () => {
    expect(comparisonLabel({ month: '2026-10', start: '2026-10-01', end: '2026-10-15' })).toBe('1-15 Oct')
  })
  it('calls a window that happens to span the whole month by its name', () => {
    // Covered to the 30th of March against 28-day February.
    expect(comparisonLabel({ month: '2026-02', start: '2026-02-01', end: '2026-02-28' })).toBe('February')
  })
  it('names a single-day window as one day', () => {
    expect(comparisonLabel({ month: '2026-10', start: '2026-10-01', end: '2026-10-01' })).toBe('1 Oct')
  })
  it('is null when there is no comparison', () => {
    expect(comparisonLabel(null)).toBeNull()
  })
})

describe('accounts that stop on different days', () => {
  const apart = {
    ...partial, counted: 2,
    short: [{ account_id: 'b', account_label: 'SBI savings', covered_through: '2026-11-15' }],
  }
  it('gives no day range, only "in progress", while the month is running', () => {
    expect(coverageLabel(apart, '2026-11-20')).toBe('in progress')
  })
  it('says the month is partly covered once it has ended', () => {
    expect(coverageLabel(apart, '2026-12-02')).toBe('partly covered')
  })
  it('still gives the range when every account stops on the same day', () => {
    const together = { ...apart, counted: 1 }
    expect(coverageLabel(together, '2026-11-20')).toBe('1-15 Nov, in progress')
  })
  it('names the month and says the days are each account\'s own in the comparison', () => {
    expect(comparisonLabel({ month: '2026-10', start: '2026-10-01', end: '2026-10-31', uniform: false })).toBe('October, same days for each account')
    expect(comparisonLabel({ month: '2026-10', start: '2026-10-01', end: '2026-10-15', uniform: true })).toBe('1-15 Oct')
  })
})
