import dayjs from 'dayjs'

// Wording for the window a month screen covers and the window it is compared
// with, in one place so Dashboard and Money Map say the same thing about the
// same fact. The server decides the windows (insights `period` and
// `comparison`); this only names them.

// "1-15 Nov": the days of a month the ledger covers so far; "1 Nov" for one day.
function dayRange(start, end) {
  const a = dayjs(start)
  const b = dayjs(end)
  if (a.isSame(b, 'day')) return `${b.date()} ${b.format('MMM')}`
  return `${a.date()}-${b.date()} ${b.format('MMM')}`
}

// A month the ledger covers only partway is "in progress" only while the
// calendar (the browser's clock, `today`) has not passed its last day. Statement
// periods end on the last transaction, so a finished month whose last
// transaction was the 28th is partial in the data but no longer in progress.
export function isInProgress(period, today = dayjs()) {
  return Boolean(period?.partial) && !dayjs(today).isAfter(dayjs(period.end), 'day')
}

// The coverage note for a partial month: "1-15 Nov, in progress" while the
// month is running, "1-28 Nov" once it has ended. Null for a fully covered month.
export function coverageLabel(period, today) {
  if (!period?.partial) return null
  const range = dayRange(period.start, period.covered_through)
  return isInProgress(period, today) ? `${range}, in progress` : range
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

// Which accounts are behind in a partial month, as the server names them: "SBI
// savings to 15 Nov". Empty when the user looks at one account (the page already
// says which) or when the month is complete.
export function shortAccountsLabel(period, { oneAccount = false } = {}) {
  if (oneAccount || !period?.partial) return ''
  const short = period.short ?? []
  // Every account counted stops on the same day: the month is just in progress, which the
  // coverage badge already says. Naming accounts is for when one is behind the others.
  if (short.length === period.counted && new Set(short.map(s => s.covered_through)).size === 1) return ''
  return short
    .map(item => `${item.account_label} to ${dayjs(item.covered_through).format('D MMM')}`)
    .join(', ')
}

// The path of /insights: the month (the server's latest when none) and the account
// (every account when none). One place, so Dashboard and Money Map ask alike.
export function insightsPath(month, accountId) {
  const params = new URLSearchParams()
  if (month) params.set('month', month)
  if (accountId) params.set('account_id', accountId)
  const query = params.toString()
  return query ? `/insights?${query}` : '/insights'
}
