// The whole comparison of J15, apart from starting the two releases: hold the
// differences between two snapshots against expected-changes.json, then run the
// checks a list of paths cannot express (invariants.js). run.js calls this, and
// compare.test.js injects regressions into a snapshot pair to prove it fails.
import { diff, explain } from './diff.js'
import { checkInvariants } from './invariants.js'

/** A payment's upi_meta is stored as JSON text; parsed, a change reads key by key. */
function parseUpiMeta(facts) {
  const parse = (row) => {
    if (typeof row.upi_meta === 'string') row.upi_meta = JSON.parse(row.upi_meta)
  }
  facts.transactions.forEach(parse)
  facts.groups.forEach((g) => g.members.forEach(parse))
}

/**
 * @param {{ facts: object, figures: object }} before the snapshot taken on the previous release
 * @param {{ facts: object, figures: object }} after the snapshot taken on the release under test
 * @param {{ version: string, from: string, changes: object[] }} expected expected-changes.json
 * @param {{ today: string, planted: object }} context the day the previous release seeded on (a
 *   YYYY-MM-DD string) and what the journey planted for the migrations to repair
 * @returns {{ problems: string[], report: string[] }}
 */
export function compareSnapshots(before, after, expected, { today, planted }) {
  const [was, now] = [structuredClone(before), structuredClone(after)]
  for (const snapshot of [was, now]) parseUpiMeta(snapshot.facts)

  const vars = {
    month: today.slice(0, 7),
    firstMonth: Object.keys(was.figures.months).sort()[0],
    ...planted.ids,
  }
  const problems = []
  const report = []
  for (const scope of ['stored', 'derived']) {
    const [a, b] = scope === 'stored' ? [was.facts, now.facts] : [was.figures, now.figures]
    const changes = diff(a, b)
    const entries = expected.changes.filter((c) => c.scope === scope)
    const { unexplained, hits, unused } = explain(changes, entries, vars)
    report.push(`\n${scope}: ${changes.length} differences, ${changes.length - unexplained.length} expected`)
    for (const [entry, matched] of hits) {
      if (matched.length > 0) report.push(`  [${entry.id}] ${matched.length} x ${[entry.path].flat().join(' | ')}`)
    }
    for (const entry of unused) problems.push(`expected change "${entry.id}" (${scope}) did not happen: remove it, or find out why it stopped`)
    for (const change of unexplained) problems.push(`unexpected ${scope} change: ${describeChange(change)}`)
  }
  problems.push(...checkInvariants(was, now))

  // What the migrations must leave alone: a merchant name the user typed, even with two slashes in it.
  const typed = now.facts.transactions.find((t) => t.id === planted.typedMerchant.id)
  if (typed?.merchant !== planted.typedMerchant.value) {
    problems.push(`the merchant "${planted.typedMerchant.value}" typed by hand on payment ${planted.typedMerchant.id} is now ${JSON.stringify(typed?.merchant)}`)
  }
  return { problems, report }
}

function describeChange(change) {
  const show = (v) => JSON.stringify(v)
  if (change.kind === 'added') return `${change.path}: added ${show(change.to)}`
  if (change.kind === 'removed') return `${change.path}: removed (was ${show(change.from)})`
  return `${change.path}: ${show(change.from)} -> ${show(change.to)}`
}
