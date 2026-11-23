// "Stored facts": everything the user put into the ledger or the app derived
// once and stored, read through the API so it does not depend on the screen.
// J8 compares one before a backup and after a restore. J15 compares one across
// an upgrade.
//
// Rows are kept whole, minus a short deny-list of fields that change on their
// own. Whole rows mean a new column is covered the day it ships; an allow-list
// would hide it. Figures computed on the fly (totals, deltas, stats) are not
// stored facts: derivedFigures() reads those.

/** Fields that move without the user doing anything. */
export const VOLATILE = [
  'updated_at', // rewritten whenever a row is edited
  'annotated_at', // rewritten whenever a row is re-annotated
  'stats', // per-person totals are computed from transactions
  'summary', // per-group totals are computed from members
  'settle_up', // who owes whom is computed from members; derivedFigures() reads it
]

/**
 * The release whose API shape differs from the current one, for the upgrade
 * journey. A version not listed here is read through the current shape, so a
 * new release needs no entry until its API changes again.
 *
 * 0.1.2 has no /policies, no /categories/tidy-up, no /accounts and no
 * /annotations/review-queue/counts (the set-aside figure is /snoozed-count).
 * 0.2.0 has all of those but /accounts.
 */
const LEGACY_0_1_2 = '0.1.2'
const RELEASE_WITHOUT_ACCOUNTS = '0.2.0'

const PAGE = 1000

async function allTransactions(api) {
  const rows = []
  let after = ''
  for (;;) {
    const page = await api.get(`/transactions?include=annotation&limit=${PAGE}${after}`)
    // The account a payment sits in is its statement's, which statements and accounts carry and
    // J15 compares; the row's own copy (added in 0.3.0 for the account column) is only that
    // statement's account read through the row.
    rows.push(...page.map(({ account_id: _id, account_label: _label, ...row }) => row))
    if (page.length < PAGE) return rows
    after = `&after=${page.at(-1).id}`
  }
}

/**
 * The account each payment row says it is in ({payment id: account id}), or null for a release whose
 * rows carry none. Not part of the stored facts (it is the statement's account read through the row,
 * and J15 compares that once, on the statements), but the upgrade checks the two agree.
 */
export async function paymentAccounts(api) {
  const found = {}
  let after = ''
  for (;;) {
    const page = await api.get(`/transactions?limit=${PAGE}${after}`)
    if (page.length > 0 && !('account_id' in page[0])) return null
    for (const row of page) found[row.id] = row.account_id
    if (page.length < PAGE) return found
    after = `&after=${page.at(-1).id}`
  }
}

const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)

function without(value, drop) {
  if (Array.isArray(value)) return value.map((v) => without(v, drop))
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).filter(([k]) => !drop.has(k)).map(([k, v]) => [k, without(v, drop)]))
  }
  return value
}

/** The three reads whose route or shape depends on the release. */
async function releaseSpecificReads(api, version) {
  if (version === LEGACY_0_1_2) {
    const { count } = await api.get('/annotations/review-queue/snoozed-count')
    // What the release cannot report is null, so a later release that can shows as a change.
    return { policies: null, tidyUp: null, queueCounts: { set_aside: count }, accounts: null }
  }
  const [policies, tidyUp, queueCounts, accounts] = await Promise.all([
    api.get('/policies'),
    api.get('/categories/tidy-up'),
    api.get('/annotations/review-queue/counts'),
    // 0.2.0 has everything above and no accounts.
    version === RELEASE_WITHOUT_ACCOUNTS ? null : api.get('/accounts'),
  ])
  return { policies, tidyUp, queueCounts, accounts }
}

/**
 * @param {object} api the app fixture's api helper
 * @param {{ ignore?: string[], version?: string }} [options] `ignore`: extra field
 *   names to leave out, for a journey where one more field legitimately changes.
 *   `version`: the release being read, when it is not the current one.
 */
export async function storedFacts(api, { ignore = [], version } = {}) {
  const drop = new Set([...VOLATILE, ...ignore])
  const [statements, transactions, people, groupList, rules, categories, config, llm, learnedRules, queue, specific] =
    await Promise.all([
      api.get('/statements'),
      allTransactions(api),
      api.get('/people'),
      api.get('/groups'),
      api.get('/rules'),
      api.get('/categories'),
      api.get('/config'),
      api.get('/config/llm'),
      api.get('/annotations/learned-rules'),
      api.get('/annotations/review-queue'),
      releaseSpecificReads(api, version),
    ])
  const { policies, tidyUp, queueCounts, accounts } = specific
  const groups = await Promise.all(groupList.map(({ id }) => api.get(`/groups/${id}`)))

  const facts = {
    statements: statements.sort(byId),
    // Where money sits: what each statement belongs to, and what the user called it.
    // null where the release has no accounts, so a release that has them shows as a change.
    accounts: accounts === null ? null : accounts.sort(byId),
    transactions: transactions.sort(byId),
    people: people.sort(byId),
    groups: groups.map((g) => ({ ...g, members: [...g.members].sort((a, b) => (a.transaction_id < b.transaction_id ? -1 : 1)) })).sort(byId),
    rules: rules.sort(byId),
    policies: policies === null ? null : policies.sort(byId),
    categories: categories.sort(byId),
    // What the app learned and what the user dismissed. A dismissed learned
    // rule or tidy-up suggestion shows up as an absence, so both lists are kept.
    learnedRules: learnedRules.sort((a, b) => (a.counterparty_key + a.direction < b.counterparty_key + b.direction ? -1 : 1)),
    tidyUp,
    // Skipped items drop out of the queue and are counted separately. The
    // queue is keyed by transaction: a row with no annotation has no annotation id.
    reviewQueue: queue.map((q) => q.transaction_id).sort(),
    reviewCounts: queueCounts,
    settings: { config, llm },
  }
  return without(facts, drop)
}

const sumOf = (rows) => Math.round(rows.reduce((total, r) => total + r.amount, 0) * 100) / 100
// No comparison is null: the first month has no month before it.
const pickTotals = (prev) => (prev ? { in: prev.money_in, out: prev.money_out, invested: prev.invested, kept: prev.kept } : null)
const pickPeople = (items) => items.map(({ id, sent, received, net, count }) => ({ id, sent, received, net, count }))
// Money Map's Top payees: who, how much, how many payments (the key list the new release adds is not a figure).
const pickTopPayees = (rows) => rows.map(({ name, total, count }) => ({ name, total, count }))
const pickRecurring = (rows) => rows.map(({ name, category, amount, cadence, active }) => ({ name, category, amount, cadence, active }))

/**
 * One month of /insights as the neutral shape, per release. The mapping is
 * explicit so a payload that changes shape shows up here, and so each release's
 * figures mean what that release's screens showed.
 */
const FIGURES_FROM_INSIGHTS = {
  // 0.1.2: no recurring_summary or breakdown. The Dashboard headline said
  // "kept" for invested + kept, while its tile and Money Map said kept. The
  // Money Map header summed the active monthly rows; the Dashboard's "Spoken
  // for each month" summed every active row whatever its cadence. People were
  // not scoped to the month, and the previous month was always the whole month.
  [LEGACY_0_1_2]: (i) => ({
    in: i.verdict.money_in,
    out: i.verdict.money_out,
    invested: i.verdict.invested,
    kept: i.verdict.kept,
    keptHeadline: i.verdict.invested + i.verdict.kept,
    comparison: pickTotals(i.verdict.prev),
    whatChanged: i.what_changed,
    breakdown: i.categories.map((c) => ({ category: c.category, net: c.net })),
    breakdownExtras: null,
    recurringHeader: sumOf(i.recurring.filter((r) => r.active && r.cadence === 'monthly')),
    committedEachMonth: sumOf(i.recurring.filter((r) => r.active)),
    recurring: pickRecurring(i.recurring),
    people: pickPeople(i.people.items),
    unexplained: i.unexplained,
    topPayees: pickTopPayees(i.merchants),
  }),
  // Current: one recurring total, which both screens show.
  current: (i) => ({
    in: i.verdict.money_in,
    out: i.verdict.money_out,
    invested: i.verdict.invested,
    kept: i.verdict.kept,
    keptHeadline: i.verdict.kept,
    comparison: pickTotals(i.verdict.prev),
    whatChanged: i.what_changed,
    breakdown: i.categories.map((c) => ({ category: c.category, net: c.net })),
    breakdownExtras: i.breakdown.extras,
    recurringHeader: i.recurring_summary.monthly_total,
    committedEachMonth: i.recurring_summary.monthly_total,
    recurring: pickRecurring(i.recurring),
    people: pickPeople(i.people.items),
    unexplained: i.unexplained,
    topPayees: pickTopPayees(i.merchants),
  }),
}

/**
 * The figures the app computes from the stored facts, in one neutral shape
 * whatever the release: per month, In, Out, Invested, Kept, the category
 * breakdown, the recurring totals, the people panel, Money Map's top payees
 * and the uncategorised figure; and over all time, each person's totals and each group's summary.
 * The upgrade journey compares these before and after, so a change in how they
 * are computed shows up as a change here.
 *
 * @param {object} api the app fixture's api helper
 * @param {{ version?: string }} [options] the release being read, when it is not the current one
 */
export async function derivedFigures(api, { version } = {}) {
  const fromInsights = FIGURES_FROM_INSIGHTS[version] ?? FIGURES_FROM_INSIGHTS.current
  const { months } = await api.get('/insights')
  const perMonth = {}
  for (const month of months) perMonth[month] = fromInsights(await api.get(`/insights?month=${month}`))

  // The same fields in every release: what a person's stats and a group's
  // summary have in common. Newer fields (your share, who owes) are not
  // compared, because the old release had no such figure. Who owes whom is
  // top-level on a group in 0.1.2 and inside its summary since.
  const people = (await api.get('/people')).map(({ id, stats }) => ({ id, ...stats }))
  const groups = await Promise.all(
    (await api.get('/groups')).map(async ({ id, summary }) => {
      const group = await api.get(`/groups/${id}`)
      return {
        id,
        memberCount: summary.member_count,
        firstDate: summary.first_date,
        lastDate: summary.last_date,
        spend: summary.spend,
        received: summary.received,
        settleUp: version === LEGACY_0_1_2 ? group.settle_up : summary.settle_up,
      }
    }),
  )
  const byId = (a, b) => (a.id < b.id ? -1 : 1)
  return { months: perMonth, people: people.sort(byId), groups: groups.sort(byId) }
}
