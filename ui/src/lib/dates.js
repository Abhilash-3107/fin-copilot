// Shared date formatting so user-facing dates read consistently everywhere.
import dayjs from 'dayjs'

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
