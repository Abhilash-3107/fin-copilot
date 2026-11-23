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

// Whether every account the month was judged by is covered the same way: `short` lists the
// accounts not covered to the whole month, so when it holds all of them and they share one
// stretch (the same first and last day), the month simply runs over that stretch; otherwise
// accounts differ.
function stopTogether(period) {
  const short = period.short
  if (!short || period.counted == null) return true
  return short.length === period.counted
    && new Set(short.map(s => `${s.covered_from ?? ''}|${s.covered_through}`)).size === 1
}

// "1-15 Nov", or "from 10 Nov" when the stretch starts late and runs to the month's end.
function stretch(period, from, through) {
  if (from && through === period.end) return `from ${dayjs(from).format('D MMM')}`
  return dayRange(from ?? period.start, through)
}

// The coverage note for a partial month: "1-15 Nov, in progress" while the month is
// running, "1-28 Nov" once it has ended, when every account stops on that day. When
// the accounts stop on different days there is no one range to give: "in progress" while the
// month is running, "partly covered" once it has ended (the line below names the accounts).
// Null for a fully covered month.
export function coverageLabel(period, today) {
  if (!period?.partial) return null
  const inProgress = isInProgress(period, today)
  if (!stopTogether(period)) return inProgress ? 'in progress' : 'partly covered'
  const first = period.short?.[0]
  const range = first ? stretch(period, first.covered_from, first.covered_through) : dayRange(period.start, period.covered_through)
  return inProgress ? `${range}, in progress` : range
}

// What deltas are measured against: "October" for a whole month, "1-15 Oct"
// for the same days of it, "October, same days for each account" when each account is
// compared over its own days (the accounts are covered to different days). Null when there is
// nothing to compare with, which is also the cue to render no delta at all.
export function comparisonLabel(comparison) {
  if (!comparison) return null
  if (comparison.uniform === false) return `${dayjs(comparison.start).format('MMMM')}, same days for each account`
  const end = dayjs(comparison.end)
  if (end.isSame(end.endOf('month'), 'day') && dayjs(comparison.start).date() === 1) {
    return dayjs(comparison.start).format('MMMM')
  }
  return dayRange(comparison.start, comparison.end)
}

// Which accounts are behind in a partial month, as the server names them: "SBI savings to 15
// Nov", "SBI savings from 10 Nov", or, for an account that has had no statement since, "SBI
// savings (the statement ends 15 Oct)". Empty when the user looks at one account (the page
// already says which) or when the month is complete.
export function shortAccountsLabel(period, { oneAccount = false } = {}) {
  if (oneAccount || !period?.partial) return ''
  const short = period.short ?? []
  // Every account counted stops on the same day: the month is just in progress, which the
  // coverage badge already says. Naming accounts is for when one is behind the others.
  if (stopTogether(period)) return ''
  return short
    .map(item => {
      if (item.closed && !item.covered_from) return `${item.account_label} (the statement ends ${dayjs(item.covered_through).format('D MMM')})`
      if (item.covered_from && item.covered_through === period.end) return `${item.account_label} from ${dayjs(item.covered_from).format('D MMM')}`
      if (item.covered_from) return `${item.account_label} ${dayRange(item.covered_from, item.covered_through)}`
      return `${item.account_label} to ${dayjs(item.covered_through).format('D MMM')}`
    })
    .join(', ')
}

// Why a month has no comparison: "No comparison with October: SBI savings has no statement for
// 1-20 Oct." One line naming each account and why, or '' when there is nothing to say (the
// first month of a ledger, or a comparison that is shown).
export function comparisonNoteLabel(note, prevMonth) {
  if (!note?.length || !prevMonth) return ''
  return `No comparison with ${dayjs(prevMonth).format('MMMM')}: ${note.map(n => `${n.account_label} ${n.reason}`).join('; ')}.`
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
