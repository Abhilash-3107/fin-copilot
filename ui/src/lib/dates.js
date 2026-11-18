// Shared date formatting so user-facing dates read consistently everywhere.
import dayjs from 'dayjs'

// A single day: "05 Jun" for the current year, "05 Jun 2025" otherwise, so
// in-year dates stay compact and cross-year ones stay unambiguous.
export function formatDate(value) {
  if (!value) return ''
  const d = dayjs(value)
  if (!d.isValid()) return String(value)
  return d.year() === dayjs().year() ? d.format('DD MMM') : d.format('DD MMM YYYY')
}

// A month bucket ("2026-05" or any parseable date) as "May 2026". Keeps raw
// YYYY-MM strings out of user-facing tables.
export function formatMonth(value) {
  if (!value) return ''
  const d = dayjs(value)
  if (!d.isValid()) return String(value)
  return d.format('MMM YYYY')
}

// A compact date range, e.g. "3 - 21 Jun" within a month, "28 Jun - 4 Jul"
// across months, "Dec 2025 - Feb 2026" across years. Falls back gracefully
// when only one (or neither) bound is present.
export function formatDateRange(start, end) {
  if (!start && !end) return ''
  const a = start ? dayjs(start) : null
  const b = end ? dayjs(end) : null
  if (a && !b) return a.format('DD MMM YYYY')
  if (b && !a) return b.format('DD MMM YYYY')
  if (a.isSame(b, 'day')) return a.format('DD MMM YYYY')
  if (!a.isSame(b, 'year')) return `${a.format('MMM YYYY')} - ${b.format('MMM YYYY')}`
  if (!a.isSame(b, 'month')) return `${a.format('DD MMM')} - ${b.format('DD MMM YYYY')}`
  return `${a.format('DD')} - ${b.format('DD MMM YYYY')}`
}

// The days a statement covers: the period its header prints when it was read,
// else the dates of its first and last payment. Null for a statement with
// neither (the sample data), which is known only by its month.
function statementDays(statement) {
  const start = statement.printed_start ?? statement.period_start
  const end = statement.printed_end ?? statement.period_end
  return start && end ? { start: dayjs(start), end: dayjs(end), printed: Boolean(statement.printed_start) } : null
}

// What a statement covers, in words: "Oct 2026" for a whole calendar month (or
// for payments that all fall in one month with no printed period to say more),
// otherwise the range, "1 Oct to 15 Nov".
export function statementSpan(statement) {
  const days = statementDays(statement)
  if (!days) return formatMonth(statement.statement_month)
  const { start, end, printed } = days
  const sameMonth = start.isSame(end, 'month')
  if (sameMonth && (!printed || (start.date() === 1 && end.isSame(end.endOf('month'), 'day')))) {
    return formatMonth(start)
  }
  const crossesYears = !start.isSame(end, 'year')
  const show = d => d.format(crossesYears ? 'D MMM YYYY' : 'D MMM')
  return `${show(start)} to ${show(end)}`
}

// Whether any of a statement's payments can fall in the month ("2026-10"): by
// its payment dates, so a statement that runs into the next month covers both.
export function statementCoversMonth(statement, month) {
  if (!statement.period_start || !statement.period_end) return statement.statement_month === month
  return statement.period_start.slice(0, 7) <= month && month <= statement.period_end.slice(0, 7)
}
