import { describe, it, expect } from 'vitest'
import { formatDate, formatMonth, formatDateRange, statementSpan, statementCoversMonth, statementLatestMonth } from './dates.js'

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

describe('statementSpan', () => {
  const stmt = fields => ({ statement_month: '2026-10', ...fields })

  it('says the month for a whole calendar month', () => {
    expect(statementSpan(stmt({ printed_start: '2026-10-01', printed_end: '2026-10-31', period_start: '2026-10-02', period_end: '2026-10-28' })))
      .toBe('Oct 2026')
  })

  it('says the month for payments within one month when no period is printed', () => {
    expect(statementSpan(stmt({ period_start: '2026-10-03', period_end: '2026-10-28' }))).toBe('Oct 2026')
  })

  it('says the range for a printed period that is part of a month', () => {
    expect(statementSpan(stmt({ printed_start: '2026-10-01', printed_end: '2026-10-08', period_start: '2026-10-01', period_end: '2026-10-08' })))
      .toBe('1 Oct to 8 Oct')
  })

  it('says the range for a statement that runs into the next month', () => {
    expect(statementSpan(stmt({ printed_start: '2026-10-05', printed_end: '2026-11-15', period_start: '2026-10-05', period_end: '2026-11-15' })))
      .toBe('5 Oct to 15 Nov')
  })

  it('uses the payment dates when no period is printed', () => {
    expect(statementSpan(stmt({ period_start: '2026-10-05', period_end: '2026-11-15' }))).toBe('5 Oct to 15 Nov')
  })

  it('puts the years on a range that crosses a year', () => {
    expect(statementSpan(stmt({ period_start: '2026-12-20', period_end: '2027-01-10' }))).toBe('20 Dec 2026 to 10 Jan 2027')
  })

  it('falls back to the statement month when there are no dates at all', () => {
    expect(statementSpan(stmt({ period_start: null, period_end: null }))).toBe('Oct 2026')
  })
})

describe('statementCoversMonth', () => {
  const spanning = { statement_month: '2026-10', period_start: '2026-10-05', period_end: '2026-11-15' }

  it('covers every month between its first and last payment', () => {
    expect(statementCoversMonth(spanning, '2026-10')).toBe(true)
    expect(statementCoversMonth(spanning, '2026-11')).toBe(true)
    expect(statementCoversMonth(spanning, '2026-09')).toBe(false)
    expect(statementCoversMonth(spanning, '2026-12')).toBe(false)
  })

  it('goes by the months its own payments are in, not the days its file covered', () => {
    // 5 Oct to 15 Nov, but every payment it added is in November.
    const overlap = { ...spanning, payment_months: ['2026-11'] }
    expect(statementCoversMonth(overlap, '2026-11')).toBe(true)
    expect(statementCoversMonth(overlap, '2026-10')).toBe(false)
  })

  it('covers no month when it has no payments', () => {
    expect(statementCoversMonth({ ...spanning, payment_months: [] }, '2026-10')).toBe(false)
  })

  it('knows a statement with no dates by its month', () => {
    const bare = { statement_month: '2026-10', period_start: null, period_end: null }
    expect(statementCoversMonth(bare, '2026-10')).toBe(true)
    expect(statementCoversMonth(bare, '2026-11')).toBe(false)
  })
})

describe('statementLatestMonth', () => {
  it('is the latest month that has the statement\'s own payments', () => {
    expect(statementLatestMonth({ statement_month: '2026-10', payment_months: ['2026-10', '2026-11'] })).toBe('2026-11')
    expect(statementLatestMonth({ statement_month: '2026-10', payment_months: ['2026-11'] })).toBe('2026-11')
  })
  it('falls back to its month when the list does not say', () => {
    expect(statementLatestMonth({ statement_month: '2026-10' })).toBe('2026-10')
    expect(statementLatestMonth({ statement_month: '2026-10', payment_months: [] })).toBe('2026-10')
  })
})
