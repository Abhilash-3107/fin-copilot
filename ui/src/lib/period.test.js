import { describe, it, expect } from 'vitest'
import { inProgressLabel, comparisonLabel } from './period.js'

const complete = { month: '2026-10', start: '2026-10-01', end: '2026-10-31', covered_through: '2026-10-31', partial: false }
const partial = { month: '2026-11', start: '2026-11-01', end: '2026-11-30', covered_through: '2026-11-15', partial: true }

describe('inProgressLabel', () => {
  it('names the covered days of a partial month', () => {
    expect(inProgressLabel(partial)).toBe('1-15 Nov, in progress')
  })
  it('is null for a complete month or no period', () => {
    expect(inProgressLabel(complete)).toBeNull()
    expect(inProgressLabel(undefined)).toBeNull()
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
  it('is null when there is no comparison', () => {
    expect(comparisonLabel(null)).toBeNull()
  })
})
