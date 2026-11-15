// What a list of changed paths cannot say about an upgrade. expected-changes.json
// says WHERE a value may change; each check here says what the new value must be
// in relation to the old one, so a change that happens in an allowed place but
// is wrong (a payee renamed to something else, an id pointing at the wrong
// category, a panel emptied) still fails.
//
// Every check takes the two snapshots (see run.js: `facts` and `figures`, with
// each payment's upi_meta already parsed by compare.js) and returns the problems
// it finds, as sentences.

const cents = (n) => Math.round(n * 100) / 100
const sum = (rows, pick) => cents(rows.reduce((total, row) => total + pick(row), 0))
const isSample = (id) => id.startsWith('demo_')

/** Migration 036 only ever writes the id of the category or subcategory the label names. */
function categoryIdsNameTheirLabel(before, after) {
  const problems = []
  const categories = new Map(after.facts.categories.map((c) => [c.id, c]))
  const payments = new Map(after.facts.transactions.map((t) => [t.id, t]))
  for (const row of after.facts.annotationCategoryIds) {
    const payment = payments.get(row.transaction_id)
    if (!payment) {
      problems.push(`an annotation belongs to payment ${row.transaction_id}, which is not in the ledger`)
      continue
    }
    const category = row.category_id === null ? null : categories.get(row.category_id)
    if (row.category_id !== null && (!category || category.parent_id !== null || category.name !== payment.category)) {
      problems.push(`payment ${payment.id} is labelled "${payment.category}" but its category id ${row.category_id} names "${category?.name}"`)
    }
    const sub = row.subcategory_id === null ? null : categories.get(row.subcategory_id)
    if (row.subcategory_id !== null && (!sub || sub.name !== payment.subcategory || sub.parent_id !== row.category_id)) {
      problems.push(
        `payment ${payment.id} is labelled "${payment.category} / ${payment.subcategory}" but its subcategory id ${row.subcategory_id} names "${sub?.name}"`,
      )
    }
  }
  return problems
}

/**
 * A payment's upi_meta may only gain keys (the bank and the shape the parser
 * recognised) and, for sample data, replace the hand-written note. Key by key
 * this is what the expected changes say; what is left is a meta that vanishes.
 */
function upiMetaOnlyLosesSampleNotes(before, after) {
  const problems = []
  const wasEmpty = (meta) => meta === null || meta === undefined
  const check = (label, id, was, now) => {
    if (wasEmpty(was) || !wasEmpty(now)) return
    const lost = Object.keys(was).filter((key) => key !== 'note')
    if (!isSample(id) || lost.length > 0) problems.push(`${label} ${id} lost its upi_meta (${JSON.stringify(was)})`)
  }
  const now = new Map(after.facts.transactions.map((t) => [t.id, t]))
  for (const t of before.facts.transactions) if (now.has(t.id)) check('payment', t.id, t.upi_meta, now.get(t.id).upi_meta)
  for (const g of before.facts.groups) {
    const kept = after.facts.groups.find((x) => x.id === g.id)
    for (const m of g.members) {
      const member = kept?.members.find((x) => x.id === m.id)
      if (member) check(`group ${g.id} member`, m.id, m.upi_meta, member.upi_meta)
    }
  }
  return problems
}

/** The people panel is the month's share of what it used to show for all time under every month. */
function peopleFiguresAddUpToTheOldTotals(before, after) {
  const problems = []
  const old = new Map()
  for (const month of Object.values(before.figures.months)) for (const p of month.people) if (!old.has(p.id)) old.set(p.id, p)
  const ids = new Set([...old.keys(), ...Object.values(after.figures.months).flatMap((m) => m.people.map((p) => p.id))])
  for (const id of ids) {
    const rows = Object.values(after.figures.months).flatMap((m) => m.people.filter((p) => p.id === id))
    for (const field of ['sent', 'received', 'net', 'count']) {
      const was = old.get(id)?.[field] ?? 0
      const now = sum(rows, (p) => p[field])
      if (now !== cents(was)) problems.push(`person ${id}: ${field} over all months is ${now} after the upgrade but was ${was} for all time before`)
    }
  }
  return problems
}

/** Unsorted only widened (a payment with no label now counts), so it cannot shrink. */
function unsortedOnlyGrows(before, after) {
  const problems = []
  for (const [month, now] of Object.entries(after.figures.months)) {
    const was = before.figures.months[month]?.unexplained
    if (!was) continue
    if (now.unexplained.count < was.count) problems.push(`${month}: ${now.unexplained.count} unsorted payments after, ${was.count} before`)
    if (now.unexplained.total < was.total) problems.push(`${month}: ${now.unexplained.total} unsorted after, ${was.total} before`)
  }
  return problems
}

/** The headline, the recurring total and the breakdown must agree with the figures beside them. */
function figuresAgreeWithEachOther(before, after) {
  const problems = []
  for (const [month, m] of Object.entries(after.figures.months)) {
    if (m.keptHeadline !== m.kept) problems.push(`${month}: the headline says ${m.keptHeadline} kept, the Kept figure is ${m.kept}`)
    if (m.committedEachMonth !== m.recurringHeader) {
      problems.push(`${month}: "spoken for each month" is ${m.committedEachMonth}, the Money Map recurring header is ${m.recurringHeader}`)
    }
    const rows = sum(m.breakdown, (r) => r.net) + sum(m.breakdownExtras ?? [], (r) => r.total)
    if (cents(rows) !== cents(m.out)) problems.push(`${month}: where it went adds up to ${cents(rows)}, Out is ${m.out}`)
  }
  return problems
}

/** Teach Me holds every payment with no category, so each one it newly holds had none. */
function queueOnlyGainsUnsortedPayments(before, after) {
  const unsorted = new Set(
    before.facts.transactions.filter((t) => t.annotation_id === null || t.category === null || t.category === 'Uncategorized').map((t) => t.id),
  )
  const held = new Set(before.facts.reviewQueue)
  return after.facts.reviewQueue
    .filter((id) => !held.has(id) && !unsorted.has(id))
    .map((id) => `Teach Me newly holds payment ${id}, which already had a category`)
}

/** @returns {string[]} */
export function checkInvariants(before, after) {
  return [
    ...categoryIdsNameTheirLabel(before, after),
    ...upiMetaOnlyLosesSampleNotes(before, after),
    ...peopleFiguresAddUpToTheOldTotals(before, after),
    ...unsortedOnlyGrows(before, after),
    ...figuresAgreeWithEachOther(before, after),
    ...queueOnlyGainsUnsortedPayments(before, after),
  ]
}
