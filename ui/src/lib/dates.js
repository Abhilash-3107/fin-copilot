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
