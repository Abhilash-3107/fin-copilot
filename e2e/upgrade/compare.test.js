// The comparison of J15 has to fail on a damaged upgrade, not only pass on a
// good one. This builds a small ledger as 0.1.2 leaves it, applies to it exactly
// what 0.2.0 declares in expected-changes.json, checks the comparison is silent,
// and then breaks the result in one way at a time. Each break is a regression
// that once passed the journey because the expected changes were too permissive.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { compareSnapshots } from './compare.js'

const expected = JSON.parse(readFileSync(new URL('./expected-changes.json', import.meta.url), 'utf8'))
const today = '2026-11-15'
const BANK_LINE = 'UPI/RAVI KUMAR SHA/120777386275/self'
const TYPED = 'Chai/Stall/Koramangala'
const planted = {
  ids: { closingBalanceRow: 'R3', bankLineRow: 'R4', duplicateSubcategory: 'dup_restaurants' },
  typedMerchant: { id: 'R5', value: TYPED },
}

const MIGRATIONS = [
  '027_strip_balance_label_from_descriptions', '028_embedding_text_hash', '029_suppressions_per_direction', '030_clear_bank_line_merchants',
  '031_review_policies', '032_annotation_source_policy', '033_drop_unused_duplicate_subcategories', '034_taxonomy_dismissals',
  '035_review_snooze_on_transactions', '036_backfill_annotation_category_ids', '037_payee_names', '038_review_undo',
  '039_annotation_job_scope', '040_annotation_job_attempts',
]
const CHAI = { name: 'Chai Wala Corner', category: null, amount: 30, cadence: 'monthly', active: true }

const meta = (m) => JSON.stringify(m)
const payment = (id, over = {}) => ({
  id, statement_id: 's', txn_date: '2026-08-02', amount: 100, debit_credit: 'debit', raw_description: `UPI/${id}`, running_balance: 0,
  upi_meta: null, counterparty_key: null, annotation_id: `a_${id}`, merchant: null, category: 'Food & Dining', subcategory: 'Restaurants',
  tags: [], confidence: 1, source: 'manual', original_source: null, ...over,
})
const sortedPayments = (transactions) => transactions.filter((t) => t.annotation_id !== null).length
const member = (t) => ({ id: t.id, transaction_id: t.id, upi_meta: t.upi_meta })
const month = (over) => ({
  in: 1000, out: 600, invested: 100, kept: 300, keptHeadline: 400, comparison: { in: 0, out: 0, invested: 0, kept: 0 },
  whatChanged: [{ category: 'Housing', delta: 500, current: 500, previous: 0 }], breakdown: [{ category: 'Housing', net: 500 }],
  breakdownExtras: null, recurringHeader: 80, committedEachMonth: 90, unexplained: { count: 1, total: 100 },
  people: [{ id: 'p1', sent: 300, received: 100, net: -200, count: 3 }], recurring: [CHAI], ...over,
})

/** A ledger as 0.1.2 leaves it: one of everything a migration or the re-read of descriptions acts on. */
function ledgerBefore() {
  const transactions = [
    payment('R1', { upi_meta: meta({ vpa: null, ref: '4001', note: null }), counterparty_key: 'SWIGGY' }),
    payment('R2', { upi_meta: meta({ vpa: null, ref: '4002', note: 'DINNER SHARE' }), counterparty_key: 'RAHUL MEHTA', annotation_id: null, category: null, subcategory: null }),
    payment('R3', { raw_description: 'UPI/ZEPTO/ORDER/400112235/UPI Closing Balance', upi_meta: meta({ vpa: null, ref: '1', note: null }), counterparty_key: 'ZEPTO' }),
    payment('R4', { merchant: BANK_LINE, source: 'llm', category: 'Shopping', subcategory: 'General Retail' }),
    payment('R5', { merchant: TYPED, category: 'Shopping', subcategory: 'General Retail' }),
    payment('demo_1', { upi_meta: meta({ note: 'Salary August' }), category: 'Income', subcategory: 'Salary' }),
    payment('demo_2', { upi_meta: meta({ note: 'goa share' }), counterparty_key: 'RAHUL' }),
    payment('demo_3'),
    payment('R6', { raw_description: 'UPI/CHAI WALA CORNER/PAYTM QR', counterparty_key: 'CHAI WALA CORNER' }),
  ]
  const facts = {
    statements: [{ id: 's', txn_count: transactions.length, annotated_count: transactions.filter((t) => t.annotation_id !== null).length, embedded_count: 0 }],
    transactions,
    groups: [{ id: 'g1', members: [member(transactions[0]), member(transactions[5]), member(transactions[6])] }],
    annotationCategoryIds: transactions
      .filter((t) => t.annotation_id)
      .map((t) => ({ transaction_id: t.id, category_id: null, subcategory_id: null })),
    categories: [
      { id: 'cat_food', name: 'Food & Dining', parent_id: null },
      { id: 'cat_shop', name: 'Shopping', parent_id: null },
      { id: 'cat_inc', name: 'Income', parent_id: null },
      { id: 'cat_food_restaurants', name: 'Restaurants', parent_id: 'cat_food' },
      { id: 'dup_restaurants', name: 'Restaurants', parent_id: 'cat_food' },
      { id: 'cat_shop_general', name: 'General Retail', parent_id: 'cat_shop' },
      { id: 'cat_inc_salary', name: 'Salary', parent_id: 'cat_inc' },
    ],
    policies: null,
    tidyUp: null,
    learnedRules: [{ counterparty_key: 'RAHUL', category: 'Housing' }],
    reviewQueue: [],
    reviewCounts: { set_aside: 1 },
    settings: { config: { dev_mode: false, confidence_threshold: 0.85 }, llm: {} },
    schema: {
      appliedMigrations: ['026_user_rules'],
      tableRows: { payee_names: null, review_undo: null },
      addedColumns: [],
      jobAttempts: [{ id: 'job1', attempts: null }],
    },
    snoozedAt: [{ transaction_id: 'R1', review_snoozed_at: '2026-11-01 10:00:00' }],
  }
  const figures = {
    months: {
      '2026-08': month(),
      '2026-09': month({ comparison: { in: 900, out: 500, invested: 100, kept: 300 } }),
      '2026-11': month({ comparison: { in: 800, out: 400, invested: 100, kept: 300 }, whatChanged: [{ category: 'Housing', delta: 100, current: 500, previous: 400 }] }),
    },
    people: [{ id: 'p1', sent: 300 }],
    groups: [],
  }
  return { facts, figures }
}

/** What 0.2.0 does to that ledger, as expected-changes.json declares it. */
function upgrade(before) {
  const after = structuredClone(before)
  const { facts, figures } = after
  const byId = Object.fromEntries(facts.transactions.map((t) => [t.id, t]))
  const reread = {
    R1: { vpa: null, ref: '4001', note: null, bank: null, shape: 'upi/name/ref/note' },
    R2: { vpa: null, ref: '4002', note: 'DINNER SHARE', bank: null, shape: 'upi/name/ref/note' },
    R3: { vpa: null, ref: '1', note: null, bank: null, shape: 'upi/name/ref/note' },
    demo_1: null,
    demo_2: { vpa: 'RAHUL@YBL', ref: null, note: null, bank: null, shape: 'unrecognized' },
  }
  for (const [id, value] of Object.entries(reread)) byId[id].upi_meta = value && meta(value)
  for (const m of facts.groups[0].members) m.upi_meta = byId[m.id].upi_meta
  byId.R1.counterparty_key = 'SWIGGY/ORDER'
  byId.R3.counterparty_key = 'ZEPTO/ORDER'
  byId.R3.raw_description = 'UPI/ZEPTO/ORDER/400112235/UPI'
  byId.R4.merchant = null
  for (const t of facts.transactions) t.review_snoozed_at = t.id === 'R1' ? '2026-11-01 10:00:00' : null
  const ids = { Restaurants: 'cat_food_restaurants', 'General Retail': 'cat_shop_general', Salary: 'cat_inc_salary' }
  const top = { 'Food & Dining': 'cat_food', Shopping: 'cat_shop', Income: 'cat_inc' }
  for (const row of facts.annotationCategoryIds) {
    const t = byId[row.transaction_id]
    row.category_id = top[t.category]
    row.subcategory_id = ids[t.subcategory]
  }
  facts.categories = facts.categories.filter((c) => c.id !== 'dup_restaurants')
  facts.policies = []
  facts.tidyUp = { duplicates: [], orphans: [] }
  facts.learnedRules[0].direction = 'debit'
  facts.reviewQueue = ['R2']
  facts.reviewCounts = { unsorted: 1, guesses: 0, set_aside: 1, set_aside_unsorted: 0 }
  facts.settings.config.ai_active = false

  // Release 2: payee labels, the sorted count, and the schema the new migrations add.
  const printed = (t) => t.merchant ?? t.counterparty_key ?? t.raw_description.slice(4)
  for (const t of facts.transactions) {
    Object.assign(t, { display_name: null, payee_name: null, note: null, channel: 'UPI', payee: printed(t), payee_all: t.counterparty_key ? printed(t) : null })
  }
  for (const m of facts.groups[0].members) {
    const t = byId[m.id]
    for (const field of ['counterparty_key', 'merchant', 'payee', 'payee_all', 'channel', 'display_name', 'payee_name', 'note']) m[field] = t[field]
  }
  facts.statements = facts.statements.map(({ id, txn_count }) => ({ id, txn_count, sorted_count: sortedPayments(facts.transactions) }))
  facts.learnedRules[0].payee = 'RAHUL'
  facts.schema = {
    appliedMigrations: [...facts.schema.appliedMigrations, ...MIGRATIONS],
    tableRows: { payee_names: 0, review_undo: 0 },
    addedColumns: ['transactions.display_name', 'annotation_jobs.transaction_ids', 'annotation_jobs.attempts'],
    jobAttempts: [{ id: 'job1', attempts: 0 }],
  }

  const m = figures.months
  for (const month of Object.values(m)) {
    month.keptHeadline = month.kept
    month.committedEachMonth = month.recurringHeader
    month.breakdownExtras = [{ key: 'transfers', label: 'Transfers and cash', total: 100, count: 1 }]
  }
  m['2026-08'].comparison = null
  m['2026-08'].whatChanged = []
  m['2026-11'].comparison = { in: 800, out: 300, invested: 100, kept: 400 }
  m['2026-11'].whatChanged = []
  m['2026-11'].unexplained = { count: 2, total: 250 }
  m['2026-08'].people = [{ id: 'p1', sent: 100, received: 100, net: 0, count: 2 }]
  m['2026-09'].people = [{ id: 'p1', sent: 200, received: 0, net: -200, count: 1 }]
  m['2026-11'].people = []
  for (const month of Object.values(m)) month.recurring = [{ ...CHAI, name: 'CHAI WALA CORNER' }]
  return after
}

const compare = (before, after) => compareSnapshots(before, after, expected, { today, planted }).problems

test('the ledger before and the ledger after 0.2.0 agree on every figure the release keeps', () => {
  const before = ledgerBefore()
  assert.deepEqual(compare(before, upgrade(before)), [])
})

/** Each regression: what is damaged, and what the comparison must say about it. */
const regressions = {
  'backfilled ids name the wrong category': [
    (a) => a.facts.annotationCategoryIds.forEach((r) => (r.category_id = 'cat_shop')),
    /category id cat_shop names "Shopping"/,
  ],
  'backfilled subcategory ids name the wrong subcategory': [
    (a) => a.facts.annotationCategoryIds.forEach((r) => (r.subcategory_id = 'cat_inc_salary')),
    /subcategory id cat_inc_salary/,
  ],
  'a real payment loses its reference': [
    (a) => (a.facts.transactions[0].upi_meta = meta({ vpa: null, ref: null, note: null, bank: null, shape: 'upi/name/ref/note' })),
    /transactions\[R1\]\.upi_meta\.ref/,
  ],
  'a real payment loses its note': [
    (a) => (a.facts.transactions[1].upi_meta = meta({ vpa: null, ref: '4002', note: null, bank: null, shape: 'upi/name/ref/note' })),
    /transactions\[R2\]\.upi_meta\.note/,
  ],
  'a real payment loses all of its upi_meta': [
    (a) => (a.facts.transactions[0].upi_meta = null),
    /payment R1 lost its upi_meta/,
  ],
  'a sample payment loses more than its note': [
    () => {},
    /payment demo_1 lost its upi_meta/,
    (b) => (b.facts.transactions.find((t) => t.id === 'demo_1').upi_meta = meta({ note: 'Salary August', vpa: 'x@y' })),
  ],
  'a real payee is renamed': [
    (a) => (a.facts.transactions[1].counterparty_key = 'SOMEONE ELSE'),
    /transactions\[R2\]\.counterparty_key/,
  ],
  'a payee key grows by something that is not one word': [
    (a) => (a.facts.transactions[0].counterparty_key = 'SWIGGY/ORDER/4001'),
    /transactions\[R1\]\.counterparty_key/,
  ],
  'a payee key is replaced by one that merely starts like it': [
    (a) => (a.facts.transactions[0].counterparty_key = 'SWIGGYX/ORDER'),
    /transactions\[R1\]\.counterparty_key/,
  ],
  'the comparison disappears in every month': [
    (a) => Object.values(a.figures.months).forEach((m) => (m.comparison = null)),
    /months\.2026-(09|11)\.comparison/,
  ],
  'the first month keeps comparing with zeros': [
    (a) => (a.figures.months['2026-08'].comparison = { in: 0, out: 0, invested: 0, kept: 0 }),
    /did not happen/,
  ],
  'every people panel is empty': [
    (a) => Object.values(a.figures.months).forEach((m) => (m.people = [])),
    /person p1: sent over all months is 0/,
  ],
  'a person is counted in the wrong month': [
    (a) => (a.figures.months['2026-09'].people[0].sent = 150),
    /person p1: sent over all months is 250/,
  ],
  'What changed is emptied in a full month': [
    (a) => (a.figures.months['2026-09'].whatChanged = []),
    /months\.2026-09\.whatChanged\[Housing\]/,
  ],
  'the unsorted figure is set to zero': [
    (a) => Object.values(a.figures.months).forEach((m) => (m.unexplained = { count: 0, total: 0 })),
    /2026-11: 0 unsorted payments after, 1 before/,
  ],
  'the headline keeps saying kept for invested too': [
    (a) => (a.figures.months['2026-09'].keptHeadline = 400),
    /the headline says 400 kept, the Kept figure is 300/,
  ],
  'the recurring total keeps adding every charge': [
    (a) => (a.figures.months['2026-09'].committedEachMonth = 90),
    /"spoken for each month" is 90/,
  ],
  'where it went stops adding up to Out': [
    (a) => (a.figures.months['2026-09'].breakdownExtras[0].total = 5),
    /where it went adds up to 505, Out is 600/,
  ],
  'a merchant name typed by hand is cleared': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R5').merchant = null),
    /typed by hand on payment R5/,
  ],
  'the machine merchant copied from a bank line stays': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R4').merchant = BANK_LINE),
    /"stored-bank-line-merchant-cleared" \(stored\) did not happen/,
  ],
  'the balance label stays on a description': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R3').raw_description = 'UPI/ZEPTO/ORDER/400112235/UPI Closing Balance'),
    /"stored-closing-balance-stripped" \(stored\) did not happen/,
  ],
  'an unused duplicate subcategory stays': [
    (a) => a.facts.categories.push({ id: 'dup_restaurants', name: 'Restaurants', parent_id: 'cat_food' }),
    /"stored-duplicate-subcategory-dropped" \(stored\) did not happen/,
  ],
  'a skipped payment loses its snooze time': [
    (a) => (a.facts.snoozedAt = []),
    /snoozedAt\[R1\]/,
  ],
  'Teach Me newly holds a payment that had a category': [
    (a) => a.facts.reviewQueue.push('R5'),
    /Teach Me newly holds payment R5/,
  ],
  'a statement counts an unsorted payment as sorted': [
    (a) => (a.facts.statements[0].sorted_count += 1),
    /statement s reports 9 sorted payments, its payments hold 8/,
  ],
  'a machine guess below the bar counts as sorted': [
    (a) => Object.assign(a.facts.transactions.find((t) => t.id === 'R4'), { confidence: 0.3 }),
    /statement s reports 8 sorted payments, its payments hold 7/,
  ],
  'the old annotated count was not the number of labelled payments': [
    () => {},
    /reported 9 annotated payments, 8 were labelled/,
    (b) => (b.facts.statements[0].annotated_count = 9),
  ],
  'a payment is called by something other than its payee key': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R2').payee = 'RAHUL M'),
    /payment R2 is called "RAHUL M" but its payee key is "RAHUL MEHTA"/,
  ],
  'a payment is called by something other than its merchant': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R5').payee = 'Chai Stall'),
    /payment R5 is called "Chai Stall" but its merchant is/,
  ],
  'a payment is renamed by the upgrade': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R2').payee_name = 'My friend'),
    /transactions\[R2\]\.payee_name/,
  ],
  'a payment name appears on its own': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R6').display_name = 'Tea'),
    /transactions\[R6\]\.display_name/,
  ],
  'a group member disagrees with its payment': [
    (a) => (a.facts.groups[0].members[0].payee = 'Other'),
    /group g1 member R1: payee is "Other", its payment says "SWIGGY\/ORDER"/,
  ],
  'a learned rule shows another payee': [
    (a) => (a.facts.learnedRules[0].payee = 'Someone else'),
    /the learned rule for RAHUL shows "Someone else"/,
  ],
  'a recurring charge is renamed to something else': [
    (a) => (a.figures.months['2026-09'].recurring[0].name = 'Tea stall'),
    /2026-09: the recurring row "Tea stall" is not what any payment is called/,
  ],
  'a recurring charge changes its amount with its name': [
    (a) => (a.figures.months['2026-09'].recurring[0].amount = 31),
    /months\.2026-09\.recurring\[CHAI WALA CORNER\]/,
  ],
  'a recurring charge goes missing': [
    (a) => (a.figures.months['2026-09'].recurring = []),
    /2026-09: 1 recurring rows before and 0 after/,
  ],
  'a migration does not run': [
    (a) => (a.facts.schema.appliedMigrations = a.facts.schema.appliedMigrations.filter((m) => !m.startsWith('040'))),
    /"stored-migration-040" \(stored\) did not happen/,
  ],
  'a table a migration adds is not there': [
    (a) => (a.facts.schema.tableRows.review_undo = null),
    /"stored-review-undo-table" \(stored\) did not happen/,
  ],
  'a table a migration adds starts with rows in it': [
    (a) => (a.facts.schema.tableRows.payee_names = 3),
    /schema\.tableRows\.payee_names/,
  ],
  'a sorting job starts with an attempt already counted': [
    (a) => (a.facts.schema.jobAttempts[0].attempts = 1),
    /schema\.jobAttempts\[job1\]\.attempts/,
  ],
}

for (const [name, [damage, message, preparation]] of Object.entries(regressions)) {
  test(`J15 fails when ${name}`, () => {
    const before = ledgerBefore()
    preparation?.(before)
    const after = upgrade(before)
    damage(after)
    const problems = compare(before, after)
    assert.ok(problems.length > 0, 'the comparison passed a damaged upgrade')
    assert.ok(problems.some((p) => message.test(p)), `no problem matches ${message}:\n${problems.join('\n')}`)
  })
}
