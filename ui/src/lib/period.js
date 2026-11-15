import dayjs from 'dayjs'

// Wording for the window a month screen covers and the window it is compared
// with, in one place so Dashboard and Money Map say the same thing about the
// same fact. The server decides the windows (insights `period` and
// `comparison`); this only names them.

// "1-15 Nov": the days of a month the ledger covers so far.
function dayRange(start, end) {
  const a = dayjs(start)
  const b = dayjs(end)
  return `${a.date()}-${b.date()} ${b.format('MMM')}`
}

// The in-progress note for a partial month ("1-15 Nov, in progress"), else null.
export function inProgressLabel(period) {
  if (!period?.partial) return null
  return `${dayRange(period.start, period.covered_through)}, in progress`
}

// What deltas are measured against: "October" for a whole month, "1-15 Oct"
// for the same days of it. Null when there is nothing to compare with, which
// is also the cue to render no delta at all.
export function comparisonLabel(comparison) {
  if (!comparison) return null
  const end = dayjs(comparison.end)
  if (end.isSame(end.endOf('month'), 'day') && dayjs(comparison.start).date() === 1) {
    return dayjs(comparison.start).format('MMMM')
  }
  return dayRange(comparison.start, comparison.end)
}
