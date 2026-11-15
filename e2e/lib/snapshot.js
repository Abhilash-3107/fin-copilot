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
  'embedded_count', // the vector store is rebuilt on demand
  'stats', // per-person totals are computed from transactions
  'summary', // per-group totals are computed from members
]

const PAGE = 1000

async function allTransactions(api) {
  const rows = []
  let after = ''
  for (;;) {
    const page = await api.get(`/transactions?include=annotation&limit=${PAGE}${after}`)
    rows.push(...page)
    if (page.length < PAGE) return rows
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

/**
 * @param {object} api the app fixture's api helper
 * @param {{ ignore?: string[] }} [options] extra field names to leave out, for a
 *   journey where one more field legitimately changes (an upgrade that re-derives it)
 */
export async function storedFacts(api, { ignore = [] } = {}) {
  const drop = new Set([...VOLATILE, ...ignore])
  const [statements, transactions, people, groupList, rules, policies, categories, config, llm, learnedRules, tidyUp, queue, queueCounts] =
    await Promise.all([
      api.get('/statements'),
      allTransactions(api),
      api.get('/people'),
      api.get('/groups'),
      api.get('/rules'),
      api.get('/policies'),
      api.get('/categories'),
      api.get('/config'),
      api.get('/config/llm'),
      api.get('/annotations/learned-rules'),
      api.get('/categories/tidy-up'),
      api.get('/annotations/review-queue'),
      api.get('/annotations/review-queue/counts'),
    ])
  const groups = await Promise.all(groupList.map(({ id }) => api.get(`/groups/${id}`)))

  const facts = {
    statements: statements.sort(byId),
    transactions: transactions.sort(byId),
    people: people.sort(byId),
    groups: groups.map((g) => ({ ...g, members: [...g.members].sort((a, b) => (a.transaction_id < b.transaction_id ? -1 : 1)) })).sort(byId),
    rules: rules.sort(byId),
    policies: policies.sort(byId),
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

/**
 * The figures the app computes from the stored facts, per month: In, Out,
 * Invested, Kept, the category breakdown, the recurring total and the people
 * panel. A later upgrade journey compares these before and after, so a change
 * in how they are computed shows up as a change here. Fields a build does not
 * have yet come back as null, so the shape is the same before and after.
 */
export async function derivedFigures(api) {
  const { months } = await api.get('/insights')
  const figures = {}
  for (const month of months) {
    const i = await api.get(`/insights?month=${month}`)
    const monthly = i.recurring.filter((r) => r.active && r.cadence === 'monthly')
    figures[month] = {
      in: i.verdict.money_in,
      out: i.verdict.money_out,
      invested: i.verdict.invested,
      kept: i.verdict.kept,
      breakdown: i.categories.map((c) => ({ category: c.category, net: c.net })),
      breakdownExtras: i.breakdown?.extras ?? null,
      recurringTotal: i.recurring_summary?.monthly_total ?? monthly.reduce((s, r) => s + r.amount, 0),
      recurring: i.recurring,
      people: i.people.items.map(({ id, sent, received, net, count }) => ({ id, sent, received, net, count })),
      unexplained: i.unexplained,
    }
  }
  return figures
}
