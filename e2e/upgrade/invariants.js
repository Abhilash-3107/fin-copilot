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

// Sources of a label a machine made and a person has not vouched for. Below the
// confidence bar such a label is a guess Teach Me asks about. This is written out
// here on purpose, apart from the app's own list (pipeline/sources.py), so the
// journey counts "sorted" without trusting the code it checks.
const MACHINE_SOURCES = new Set(['llm', 'rag_prompted', 'rag_direct', 'learned_rule', 'policy', 'rule', 'model'])
const UNSORTED_CATEGORY = 'Uncategorized'

/** A payment is sorted when it has a real category and is not a machine guess below the bar. */
function isSorted(t, bar) {
  if (t.annotation_id === null || t.category === null || t.category === UNSORTED_CATEGORY) return false
  return !(MACHINE_SOURCES.has(t.source) && t.confidence < bar)
}

/**
 * A statement's sorted count is what Teach Me is not waiting on, counted from the
 * payments themselves. The count it replaces was every labelled payment, and sorted
 * can only be fewer.
 */
function sortedCountsFollowTeachMe(before, after) {
  const problems = []
  const bar = after.facts.settings.config.confidence_threshold
  const labelled = new Map()
  for (const t of before.facts.transactions) if (t.annotation_id !== null) labelled.set(t.statement_id, (labelled.get(t.statement_id) ?? 0) + 1)
  const wasLabelled = new Map(before.facts.statements.map((s) => [s.id, s.annotated_count]))
  for (const s of after.facts.statements) {
    const mine = after.facts.transactions.filter((t) => t.statement_id === s.id)
    const sorted = mine.filter((t) => isSorted(t, bar)).length
    if (s.sorted_count !== sorted) problems.push(`statement ${s.id} reports ${s.sorted_count} sorted payments, its payments hold ${sorted}`)
    if (wasLabelled.has(s.id) && wasLabelled.get(s.id) !== (labelled.get(s.id) ?? 0)) {
      problems.push(`statement ${s.id} reported ${wasLabelled.get(s.id)} annotated payments, ${labelled.get(s.id) ?? 0} were labelled`)
    }
    if (s.sorted_count > (wasLabelled.get(s.id) ?? Infinity)) problems.push(`statement ${s.id} has ${s.sorted_count} sorted payments and had only ${wasLabelled.get(s.id)} labelled ones`)
  }
  return problems
}

// How a line names its channel, written out here on purpose (the app's own
// mapping is parsers/description.py channel_of) so the journey does not trust
// the code it checks. UPI and its reversals need something after the head.
const OTHER_CHANNELS = { NEFT: 'NEFT', IMPS: 'IMPS', RTGS: 'RTGS', POS: 'Card', ATM: 'ATM' }
const CHANNEL_WORDS = new Set([...Object.keys(OTHER_CHANNELS), 'UPI', 'REV-UPI'])

function channelOf(line) {
  const tokens = line.trim().split('/')
  const head = tokens[0].trim().toUpperCase()
  if (tokens.length >= 2 && head === 'UPI') return 'UPI'
  if (tokens.length >= 2 && head === 'REV-UPI') return 'UPI reversal'
  const first = line.replace(/\//g, ' ').trim().split(/\s+/)[0].toUpperCase()
  return OTHER_CHANNELS[first] ?? null
}

/** The line as the bank printed it, minus a leading channel word it names ("POS/", "NEFT/"). */
function withoutChannel(line) {
  const text = line.trim()
  const slash = text.indexOf('/')
  const rest = slash < 0 ? '' : text.slice(slash + 1).trim()
  return slash >= 0 && CHANNEL_WORDS.has(text.slice(0, slash).trim().toUpperCase()) && rest ? rest : text
}

/**
 * Nothing is renamed in this journey, so a payment is called by its merchant, or
 * else the payee exactly as the bank printed it, which is the payee the key names.
 * A label that is not the key's own payee (SWIGGY ORDER 412 for SWIGGY) would split
 * one payee into two names. Labels are shown as printed, so comparisons are exact,
 * with one explicit exception: a UPI handle is kept in capitals in the key and
 * shown lower-cased. A payment with no key has no payee to share: it is called its
 * printed line without the channel word, and "every payment to this payee" is nothing.
 */
function payeeLabelsNameTheirKey(before, after) {
  const problems = []
  const payments = after.facts.transactions
  const merchantsByKey = new Map()
  for (const t of payments) {
    if (!t.merchant || !t.counterparty_key) continue
    const counts = merchantsByKey.get(t.counterparty_key) ?? new Map()
    counts.set(t.merchant.trim(), (counts.get(t.merchant.trim()) ?? 0) + 1)
    merchantsByKey.set(t.counterparty_key, counts)
  }
  // What every payment to a key is called: its most common merchant (ties to the
  // first alphabetically), else the key's own payee, a UPI handle lower-cased.
  const labelOfKey = (key) => {
    const counts = merchantsByKey.get(key)
    if (counts) return [...counts].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0][0]
    return key.includes('@') ? key.toLowerCase() : key
  }
  for (const t of payments) {
    if (typeof t.payee !== 'string' || t.payee.trim() === '') problems.push(`payment ${t.id} has no name`)
    else if (t.merchant && t.payee !== t.merchant) problems.push(`payment ${t.id} is called "${t.payee}" but its merchant is "${t.merchant}"`)
    else if (!t.merchant && t.counterparty_key && !merchantsByKey.has(t.counterparty_key)) {
      if (t.payee !== labelOfKey(t.counterparty_key)) {
        problems.push(`payment ${t.id} is called "${t.payee}" but its payee key is "${t.counterparty_key}"`)
      }
    } else if (!t.merchant && !t.counterparty_key) {
      const printed = withoutChannel(t.raw_description)
      const fine = /^UPI\//i.test(t.raw_description) ? t.raw_description.toLowerCase().includes(t.payee.toLowerCase()) : t.payee === printed
      if (!fine) problems.push(`payment ${t.id} has no payee key and is called "${t.payee}", not its line "${printed}"`)
    }
    if (t.counterparty_key ? t.payee_all !== labelOfKey(t.counterparty_key) : t.payee_all !== null) {
      problems.push(`payment ${t.id} says every payment to its payee is called ${JSON.stringify(t.payee_all)}, not ${JSON.stringify(t.counterparty_key ? labelOfKey(t.counterparty_key) : null)}`)
    }
    if (t.channel !== channelOf(t.raw_description)) {
      problems.push(`payment ${t.id} has channel ${JSON.stringify(t.channel)} for the line "${t.raw_description}", which says ${JSON.stringify(channelOf(t.raw_description))}`)
    }
  }
  return problems
}

/** A group member shows the same payment as the ledger does, so every field it gained is its payment's. */
function groupMembersAgreeWithTheirPayment(before, after) {
  const problems = []
  const payments = new Map(after.facts.transactions.map((t) => [t.id, t]))
  for (const g of after.facts.groups) {
    for (const m of g.members) {
      const t = payments.get(m.transaction_id)
      if (!t) continue
      for (const field of ['counterparty_key', 'merchant', 'payee', 'payee_all', 'channel', 'display_name', 'payee_name', 'note']) {
        if (m[field] !== t[field]) problems.push(`group ${g.id} member ${m.id}: ${field} is ${JSON.stringify(m[field])}, its payment says ${JSON.stringify(t[field])}`)
      }
    }
  }
  return problems
}

/** The payee a learned rule shows is the name its payee goes by on its payments. */
function learnedRulePayeesAreThePayeesNames(before, after) {
  const problems = []
  for (const rule of after.facts.learnedRules) {
    const names = new Set(after.facts.transactions.filter((t) => t.counterparty_key === rule.counterparty_key).map((t) => t.payee_all ?? t.payee))
    if (names.size > 0 && !names.has(rule.payee)) {
      problems.push(`the learned rule for ${rule.counterparty_key} shows "${rule.payee}", its payments are called ${[...names].map((n) => `"${n}"`).join(' or ')}`)
    }
  }
  return problems
}

/**
 * Recurring rows are named after the payee. A row may change its name, in case only
 * (the bank's printing for the title case it used to get), and every name in the
 * upgraded ledger is one a payment goes by. Its amount, cadence and category never move.
 */
function recurringRowsOnlyChangeTheirCase(before, after) {
  const problems = []
  const names = new Set(after.facts.transactions.flatMap((t) => [t.payee, t.payee_all]).filter(Boolean))
  for (const [month, now] of Object.entries(after.figures.months)) {
    const was = before.figures.months[month]?.recurring ?? []
    const wasByName = new Map(was.map((r) => [r.name.toLowerCase(), r]))
    for (const row of now.recurring) {
      if (!names.has(row.name)) problems.push(`${month}: the recurring row "${row.name}" is not what any payment is called`)
      const old = wasByName.get(row.name.toLowerCase())
      if (old && JSON.stringify({ ...old, name: row.name }) !== JSON.stringify(row)) {
        problems.push(`${month}: the recurring row "${row.name}" changed more than its name (was ${JSON.stringify(old)}, is ${JSON.stringify(row)})`)
      }
    }
    if (was.length !== now.recurring.length) problems.push(`${month}: ${was.length} recurring rows before and ${now.recurring.length} after`)
  }
  return problems
}

/**
 * Top payees are the same list as before (the journey renames nothing, so no
 * expected change names them), and each is called what its payments are called.
 */
function topPayeesAreNamedLikeTheirPayments(before, after) {
  const problems = []
  const names = new Set(after.facts.transactions.flatMap((t) => [t.payee, t.payee_all]).filter(Boolean))
  for (const [month, now] of Object.entries(after.figures.months)) {
    for (const row of now.topPayees) {
      if (!names.has(row.name)) problems.push(`${month}: the top payee "${row.name}" is not what any payment is called`)
    }
    const was = before.figures.months[month]?.topPayees
    if (was && was.length !== now.topPayees.length) problems.push(`${month}: ${was.length} top payees before and ${now.topPayees.length} after`)
  }
  return problems
}

/**
 * Migration 041 puts every statement in an account: each statement names an account
 * that exists, no account is empty, the counts add up, and the label an account is
 * shown by never carries a full account number (only the last four digits).
 */
function accountsHoldEveryStatement(before, after) {
  const accounts = after.facts.accounts
  if (!accounts) return []
  const problems = []
  const byId = new Map(accounts.map((a) => [a.id, a]))
  const held = new Map()
  for (const s of after.facts.statements) {
    const account = byId.get(s.account_id)
    if (!account) {
      problems.push(`statement ${s.id} belongs to account ${s.account_id}, which is not in the ledger`)
      continue
    }
    held.set(account.id, (held.get(account.id) ?? 0) + 1)
    if (s.account_label !== account.account_label) {
      problems.push(`statement ${s.id} calls its account "${s.account_label}", the account is called "${account.account_label}"`)
    }
  }
  for (const a of accounts) {
    if ((held.get(a.id) ?? 0) === 0) problems.push(`account ${a.id} holds no statement`)
    else if (a.statement_count !== held.get(a.id)) problems.push(`account ${a.id} says it has ${a.statement_count} statements, ${held.get(a.id)} belong to it`)
    if (/\d{5,}/.test(a.account_label) || (a.account_ref_last4 ?? '').length > 4) problems.push(`account ${a.id} shows more than the last four digits of its number`)
  }
  return problems
}

/** @returns {string[]} */
export function checkInvariants(before, after) {
  return [
    ...accountsHoldEveryStatement(before, after),
    ...sortedCountsFollowTeachMe(before, after),
    ...payeeLabelsNameTheirKey(before, after),
    ...groupMembersAgreeWithTheirPayment(before, after),
    ...learnedRulePayeesAreThePayeesNames(before, after),
    ...recurringRowsOnlyChangeTheirCase(before, after),
    ...topPayeesAreNamedLikeTheirPayments(before, after),
    ...categoryIdsNameTheirLabel(before, after),
    ...upiMetaOnlyLosesSampleNotes(before, after),
    ...peopleFiguresAddUpToTheOldTotals(before, after),
    ...unsortedOnlyGrows(before, after),
    ...figuresAgreeWithEachOther(before, after),
    ...queueOnlyGainsUnsortedPayments(before, after),
  ]
}
