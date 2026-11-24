// The comparison of J15 has to fail on a damaged upgrade, not only pass on a
// good one. This builds a small ledger as 0.1.2 leaves it, applies to it exactly
// what the release declares in expected-changes.json, checks the comparison is silent,
// and then breaks the result in one way at a time. Each break is a regression
// that once passed the journey because the expected changes were too permissive.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { compareSnapshots } from './compare.js'

const expected = JSON.parse(readFileSync(new URL('./expected-changes.json', import.meta.url), 'utf8'))
const BANK_LINE = 'UPI/RAVI KUMAR SHA/120777386275/self'
const TYPED = 'Chai/Stall/Koramangala'
const planted = {
  // The month the journey's own account first appears in: a month with no comparison, and a reason why.
  ids: { closingBalanceRow: 'R3', bankLineRow: 'R4', duplicateSubcategory: 'dup_restaurants', accountMonth: '2026-10' },
  typedMerchant: { id: 'R5', value: TYPED },
}

const MIGRATIONS = [
  '027_strip_balance_label_from_descriptions', '028_embedding_text_hash', '029_suppressions_per_direction', '030_clear_bank_line_merchants',
  '031_review_policies', '032_annotation_source_policy', '033_drop_unused_duplicate_subcategories', '034_taxonomy_dismissals',
  '035_review_snooze_on_transactions', '036_backfill_annotation_category_ids', '037_payee_names', '038_review_undo',
  '039_annotation_job_scope', '040_annotation_job_attempts', '041_accounts', '042_payment_notes', '043_statement_row_counts', '044_transfer_pairing', '045_import_layouts',
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
  in: 1000, out: 600, invested: 100, kept: 300, keptHeadline: 400, comparison: { in: 0, out: 0, invested: 0, kept: 0 }, comparisonNote: [],
  whatChanged: [{ category: 'Housing', delta: 500, current: 500, previous: 0 }], breakdown: [{ category: 'Housing', net: 500 }],
  breakdownExtras: null, otherIn: 0, recurringHeader: 80, committedEachMonth: 90, unexplained: { count: 1, total: 100 },
  people: [{ id: 'p1', sent: 300, received: 100, net: -200, count: 3 }], recurring: [CHAI],
  topPayees: [{ name: 'BigBasket', total: 100, count: 1 }], ...over,
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
    // Lines with no payee key and no merchant: the app calls them by their printed line.
    payment('R7', { raw_description: 'NEFT/ACME LABS PVT LTD/SALARY OCT' }),
    payment('R8', { raw_description: 'POS/EXAMPLE BOOKSTORE BANGALORE' }),
    payment('R9', { raw_description: 'UPI/9988776655@OKAXIS', counterparty_key: '9988776655@OKAXIS' }),
    payment('R10', { raw_description: 'UPI/BIGBASKET/GROCERIES', counterparty_key: 'BIGBASKET', merchant: 'BigBasket' }),
  ]
  const facts = {
    statements: [
      { id: 's', bank_name: 'generic', account_ref: 'XXXX0000', statement_month: '2026-08', uploaded_at: '2026-08-03 10:00:00', closing_balance: 0, period_end: '2026-08-02', balance_check: { checked: 12, consistent: 12, balanced: true }, txn_count: transactions.length, annotated_count: transactions.filter((t) => t.annotation_id !== null).length, embedded_count: 0 },
      // A sample statement: a bank, and no account number.
      { id: 'demo_s', bank_name: 'kotak', account_ref: null, statement_month: '2026-08', uploaded_at: '2026-08-04 10:00:00', closing_balance: null, period_end: null, balance_check: { checked: 0, consistent: 0, balanced: true }, txn_count: 0, annotated_count: 0, embedded_count: 0 },
    ],
    accounts: null,
    transactions,
    groups: [{ id: 'g1', members: [member(transactions[0]), member(transactions[5]), member(transactions[6])] }],
    annotationRows: transactions
      .filter((t) => t.annotation_id)
      .map((t) => ({
        id: t.annotation_id, transaction_id: t.id, merchant: t.merchant, category: t.category, subcategory: t.subcategory,
        tags: JSON.stringify(t.tags), confidence: t.confidence, source: t.source, original_source: t.original_source,
        category_id: null, subcategory_id: null, reasoning: '{"stage":"x"}', annotated_at: '2026-08-02 10:00:00',
      })),
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
      tableRows: { payee_names: null, review_undo: null, accounts: null, transfer_unpaired: null, import_layouts: null },
      addedColumns: [],
      jobAttempts: [{ id: 'job1', attempts: null }],
    },
    snoozedAt: [{ transaction_id: 'R1', review_snoozed_at: '2026-11-01 10:00:00' }],
  }
  const figures = {
    months: {
      '2026-08': month(),
      '2026-09': month({ comparison: { in: 900, out: 500, invested: 100, kept: 300 } }),
      '2026-10': month({ otherIn: 3361, comparison: { in: 900, out: 500, invested: 100, kept: 300 }, whatChanged: [{ category: 'Housing', delta: 50, current: 500, previous: 450 }] }),
      '2026-11': month({ otherIn: 85450, comparison: { in: 800, out: 400, invested: 100, kept: 300 }, whatChanged: [{ category: 'Housing', delta: 100, current: 500, previous: 400 }] }),
    },
    people: [{ id: 'p1', sent: 300 }],
    groups: [],
  }
  return { facts, figures }
}

/** What the release does to that ledger, as expected-changes.json declares it. */
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
  facts.annotationRows.find((r) => r.transaction_id === 'R4').merchant = null
  for (const t of facts.transactions) t.review_snoozed_at = t.id === 'R1' ? '2026-11-01 10:00:00' : null
  const ids = { Restaurants: 'cat_food_restaurants', 'General Retail': 'cat_shop_general', Salary: 'cat_inc_salary' }
  const top = { 'Food & Dining': 'cat_food', Shopping: 'cat_shop', Income: 'cat_inc' }
  for (const row of facts.annotationRows) {
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
    Object.assign(t, { display_name: null, payee_name: null, note: null, user_note: null, note_is_yours: false, transfer: null, waiting_on_pair: false, channel: 'UPI', payee: printed(t), payee_all: t.counterparty_key ? printed(t) : null })
  }
  Object.assign(byId.R7, { payee: 'ACME LABS PVT LTD/SALARY OCT', channel: 'NEFT' })
  Object.assign(byId.R8, { payee: 'EXAMPLE BOOKSTORE BANGALORE', channel: 'Card' })
  Object.assign(byId.R9, { payee: '9988776655@okaxis', payee_all: '9988776655@okaxis' }) // a UPI handle is shown lower-cased
  for (const m of facts.groups[0].members) {
    const t = byId[m.id]
    for (const field of ['counterparty_key', 'merchant', 'payee', 'payee_all', 'channel', 'display_name', 'payee_name', 'note', 'user_note', 'note_is_yours']) m[field] = t[field]
  }
  const owner = { s: { id: 'acc_s', label: 'Bank ··0000', last4: '0000' }, demo_s: { id: 'acc_demo_s', label: 'Kotak', last4: null } }
  facts.statements = facts.statements.map(({ id, txn_count, bank_name, statement_month, uploaded_at, closing_balance, period_end, balance_check }) => ({
    id, bank_name, statement_month, uploaded_at, closing_balance, period_end, txn_count, sorted_count: id === 's' ? sortedPayments(facts.transactions) : 0,
    // Release 3: what the file held and added (every payment, for a statement that was already here) and its stored balance check.
    balance_check, rows_in_file: txn_count, rows_added: txn_count, balance_checked: balance_check.checked, balance_consistent: balance_check.consistent, shared_with_others: 0,
    // Where the statement's own payments are, as the payments say.
    first_payment: id === 's' ? '2026-08-02' : null, last_payment: id === 's' ? '2026-08-02' : null, payment_months: id === 's' ? ['2026-08'] : [],
    account_id: owner[id].id, account_label: owner[id].label, account_ref_last4: owner[id].last4, printed_start: null, printed_end: null,
  }))
  facts.accounts = [
    {
      id: 'acc_s', account_label: 'Bank ··0000', name: null, bank_name: 'generic', account_ref_last4: '0000', statement_count: 1,
      first_date: '2026-08-02', last_date: '2026-08-02', balance: 0, balance_as_of: '2026-08-02',
    },
    {
      id: 'acc_demo_s', account_label: 'Kotak', name: null, bank_name: 'kotak', account_ref_last4: null, statement_count: 1,
      first_date: null, last_date: null, balance: null, balance_as_of: null,
    },
  ]
  facts.learnedRules[0].payee = 'RAHUL'
  facts.schema = {
    appliedMigrations: [...facts.schema.appliedMigrations, ...MIGRATIONS],
    tableRows: { payee_names: 0, review_undo: 0, accounts: 2, transfer_unpaired: 0, import_layouts: 0 },
    addedColumns: [
      'transactions.display_name', 'transactions.user_note', 'annotation_jobs.transaction_ids', 'annotation_jobs.attempts',
      'statements.account_id', 'statements.printed_start', 'statements.printed_end',
      'statements.rows_in_file', 'statements.rows_added', 'statements.balance_checked', 'statements.balance_consistent',
    ],
    jobAttempts: [{ id: 'job1', attempts: 0 }],
  }

  const m = figures.months
  for (const month of Object.values(m)) {
    month.keptHeadline = month.kept
    month.committedEachMonth = month.recurringHeader
    month.breakdownExtras = [{ key: 'transfers', label: 'Transfers and cash', total: 100, count: 1 }]
  }
  // Money in that is not sorted yet used to be counted as refunds and paybacks: the ledger has 750 of it in
  // October and 85,450 in November, and every month has the new figure.
  for (const month of Object.values(m)) month.unsortedIn = 0
  m['2026-10'].unsortedIn = 750
  m['2026-10'].otherIn = 2611
  m['2026-11'].unsortedIn = 85450
  m['2026-11'].otherIn = 0
  m['2026-08'].comparison = null
  m['2026-08'].whatChanged = []
  // The month the second account first appears in: nothing before it to compare it with, so no comparison, and the reason.
  m['2026-10'].comparison = null
  m['2026-10'].whatChanged = []
  m['2026-10'].comparisonNote = ['was added in October']
  m['2026-10'].people = []
  m['2026-11'].comparison = { in: 800, out: 300, invested: 100, kept: 400 }
  m['2026-11'].whatChanged = []
  m['2026-11'].unexplained = { count: 2, total: 250 }
  m['2026-08'].people = [{ id: 'p1', sent: 100, received: 100, net: 0, count: 2 }]
  m['2026-09'].people = [{ id: 'p1', sent: 200, received: 0, net: -200, count: 1 }]
  m['2026-11'].people = []
  for (const month of Object.values(m)) month.recurring = [{ ...CHAI, name: 'CHAI WALA CORNER' }]
  // Each payment row says which account it is in, which is its statement's.
  const accountOfStatement = Object.fromEntries(facts.statements.map((s) => [s.id, s.account_id]))
  after.copies = { paymentAccounts: Object.fromEntries(facts.transactions.map((t) => [t.id, accountOfStatement[t.statement_id]])) }
  return after
}

const compare = (before, after) => compareSnapshots(before, after, expected, { planted }).problems

test('the ledger before and the ledger after the release agree on every figure the release keeps', () => {
  const before = ledgerBefore()
  assert.deepEqual(compare(before, upgrade(before)), [])
})

// A recurring row to a payee with no merchant name is listed under the payee as the bank printed it,
// instead of in title case: the name moves in case and nothing else. The journey's ledger is made on a
// fixed day, so the demo's newest Chai Wala charge is always unsorted and the row is keyed by its name.
test("the recurring row's rename in case passes", () => {
  const before = ledgerBefore()
  for (const month of Object.values(before.figures.months)) month.recurring = [{ ...CHAI, category: null }]
  const after = upgrade(before)
  for (const month of Object.values(after.figures.months)) month.recurring = [{ ...CHAI, category: null, name: 'CHAI WALA CORNER' }]
  assert.deepEqual(compare(before, after), [])
})

// A rename of more than the case fails, whether the row carries a category (it is then keyed by it) or not.
for (const [shape, category] of [['no category', null], ['a category', 'Food & Dining']]) {
  test(`a recurring row that changes more than its case fails when it has ${shape}`, () => {
    const before = ledgerBefore()
    for (const month of Object.values(before.figures.months)) month.recurring = [{ ...CHAI, category }]
    const after = upgrade(before)
    for (const month of Object.values(after.figures.months)) month.recurring = [{ ...CHAI, category, name: 'CHAI WALA CORNER', amount: 31 }]
    assert.ok(compare(before, after).some((p) => /months\.2026-09\.recurring\[.*\]|changed more than its name/.test(p)))
  })
}

/** A second statement of the ledger, on both sides of the upgrade, in its own account unless the case says otherwise. */
function addStatement(after, before, id, fields = {}) {
  const base = { id, bank_name: 'generic', account_ref: 'XXXX0000', statement_month: '2026-09', uploaded_at: '2026-09-03 10:00:00', ...fields }
  const noChain = { checked: 0, consistent: 0, balanced: true }
  before.facts.statements.push({ ...base, closing_balance: null, period_end: null, balance_check: noChain, txn_count: 0, annotated_count: 0, embedded_count: 0 })
  const { account_ref: ref, ...shown } = base
  after.facts.statements.push({
    ...shown, closing_balance: null, period_end: null, txn_count: 0, sorted_count: 0, account_id: `acc_${id}`, account_label: 'Bank ··0000',
    account_ref_last4: ref ? ref.slice(-4) : null, printed_start: null, printed_end: null,
    balance_check: noChain, rows_in_file: 0, rows_added: 0, balance_checked: 0, balance_consistent: 0, shared_with_others: 0,
    first_payment: null, last_payment: null, payment_months: [],
  })
  after.facts.accounts.push({ ...after.facts.accounts[0], id: `acc_${id}`, statement_count: 1 })
  after.facts.schema.tableRows.accounts = after.facts.accounts.length
}

/** Each regression: what is damaged, and what the comparison must say about it. */
const regressions = {
  'backfilled ids name the wrong category': [
    (a) => a.facts.annotationRows.forEach((r) => (r.category_id = 'cat_shop')),
    /category id cat_shop names "Shopping"/,
  ],
  'backfilled subcategory ids name the wrong subcategory': [
    (a) => a.facts.annotationRows.forEach((r) => (r.subcategory_id = 'cat_inc_salary')),
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
  'the new account\'s month keeps comparing without it': [
    (a, b) => (a.figures.months['2026-10'].comparison = b.figures.months['2026-10'].comparison),
    /"figures-no-comparison-when-an-account-is-new" \(derived\) did not happen/,
  ],
  'the new account\'s month gives no reason': [
    (a) => (a.figures.months['2026-10'].comparisonNote = []),
    /2026-10: no comparison and no reason given/,
  ],
  'the new account\'s month keeps a What changed row': [
    (a) => (a.figures.months['2026-10'].whatChanged = [{ category: 'Housing', delta: 50, current: 500, previous: 450 }]),
    /2026-10: no comparison, yet What changed lists 1 rows/,
  ],
  'a month shows a comparison and a reason for having none': [
    (a) => (a.figures.months['2026-09'].comparisonNote = ['was added in September']),
    /2026-09: a comparison is shown and also a reason for having none/,
  ],
  'a full month is no longer compared when no account is new in it': [
    (a) => {
      a.figures.months['2026-09'].comparison = null
      a.figures.months['2026-09'].whatChanged = []
    },
    /months\.2026-09\.comparison|2026-09: no comparison and no reason given/,
  ],
  'a What changed delta in a full month is off by 777': [
    (a) => (a.figures.months['2026-09'].whatChanged[0].delta += 777),
    /months\.2026-09\.whatChanged\[Housing\]\.delta|Housing moved by 1277/,
  ],
  'a What changed figure in a full month is off by 777': [
    (a) => (a.figures.months['2026-09'].whatChanged[0].current += 777),
    /months\.2026-09\.whatChanged\[Housing\]\.current/,
  ],
  'a What changed previous figure in a full month is off by 777': [
    (a) => (a.figures.months['2026-09'].whatChanged[0].previous += 777),
    /months\.2026-09\.whatChanged\[Housing\]\.previous/,
  ],
  'the comparison\'s Out in a full month is off by 777': [
    (a) => (a.figures.months['2026-09'].comparison.out += 777),
    /months\.2026-09\.comparison\.out/,
  ],
  'the previous month\'s Kept is not measured from the same payments as the other tiles': [
    (a, b) => {
      a.figures.months['2026-09'].comparison.kept = 999
      b.figures.months['2026-09'].comparison.kept = 999
    },
    /the previous month's Kept 999 is not its In - Out - Invested/,
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
  'a statement names an account that is not there': [
    (a) => (a.facts.statements[0].account_id = 'acc_gone'),
    /statement s belongs to account acc_gone, which is not in the ledger/,
  ],
  // The payment rows do not carry their account in the snapshot (it is their statement's), so a
  // statement moved to another account must still show.
  'a statement is in another account than it was': [
    (a) => (a.facts.statements[0].account_id = 'acc_demo_s'),
    /payment R1 sits in account acc_s, its statement s is in acc_demo_s/,
  ],
  'a payment row says another account than its statement': [
    (a) => (a.copies.paymentAccounts.R1 = 'acc_demo_s'),
    /payment R1 sits in account acc_demo_s, its statement s is in acc_s/,
  ],
  'a payment row says no account': [
    (a) => delete a.copies.paymentAccounts.R1,
    /payment R1 has no account on its row/,
  ],
  'an account holds no statement': [
    (a) => a.facts.accounts.push({ ...a.facts.accounts[0], id: 'acc_empty' }),
    /account acc_empty holds no statement/,
  ],
  'an account miscounts its statements': [
    (a) => (a.facts.accounts[0].statement_count = 3),
    /account acc_s says it has 3 statements, 1 belong to it/,
  ],
  'an account is shown by its whole number': [
    (a) => (a.facts.accounts[0].account_label = 'Bank 3250508074'),
    /account acc_s shows more than the last four digits of its number/,
  ],
  'a statement counts an unsorted payment as sorted': [
    (a) => (a.facts.statements[0].sorted_count += 1),
    /statement s reports 13 sorted payments, its payments hold 12/,
  ],
  'a machine guess below the bar counts as sorted': [
    (a) => Object.assign(a.facts.transactions.find((t) => t.id === 'R4'), { confidence: 0.3 }),
    /statement s reports 12 sorted payments, its payments hold 11/,
  ],
  'the old annotated count was not the number of labelled payments': [
    () => {},
    /reported 13 annotated payments, 12 were labelled/,
    (b) => (b.facts.statements[0].annotated_count = 13),
  ],
  'a payment is called by something other than its payee key': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R2').payee = 'RAHUL M'),
    /payment R2 is called "RAHUL M" but its payee key is "RAHUL MEHTA"/,
  ],
  'a payee label differs from its key only in case': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R2').payee = 'Rahul Mehta'),
    /payment R2 is called "Rahul Mehta" but its payee key is "RAHUL MEHTA"/,
  ],
  'a UPI handle is not lower-cased': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R9').payee = '9988776655@OKAXIS'),
    /payment R9 is called "9988776655@OKAXIS" but its payee key is "9988776655@OKAXIS"/,
  ],
  'a payment with no key is called by another payee': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R8').payee = 'Swiggy'),
    /payment R8 has no payee key and is called "Swiggy", not its line "EXAMPLE BOOKSTORE BANGALORE"/,
  ],
  'a payment with no key keeps its channel word in its name': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R7').payee = 'NEFT/ACME LABS PVT LTD/SALARY OCT'),
    /payment R7 has no payee key and is called "NEFT\/ACME LABS PVT LTD\/SALARY OCT", not its line "ACME LABS PVT LTD\/SALARY OCT"/,
  ],
  'a payment with no key claims a payee as a whole': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R8').payee_all = 'EXAMPLE BOOKSTORE BANGALORE'),
    /payment R8 says every payment to its payee is called "EXAMPLE BOOKSTORE BANGALORE", not null/,
  ],
  'a payee as a whole is not the key\'s label': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R2').payee_all = 'RAHUL'),
    /payment R2 says every payment to its payee is called "RAHUL", not "RAHUL MEHTA"/,
  ],
  'a payee as a whole drops the merchant its key is called': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R10').payee_all = 'BIGBASKET'),
    /payment R10 says every payment to its payee is called "BIGBASKET", not "BigBasket"/,
  ],
  'a card payment is called a NEFT one': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R8').channel = 'NEFT'),
    /payment R8 has channel "NEFT" for the line "POS\/EXAMPLE BOOKSTORE BANGALORE", which says "Card"/,
  ],
  'a line with no channel word is given one': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R7').channel = null),
    /payment R7 has channel null for the line "NEFT\/ACME LABS PVT LTD\/SALARY OCT", which says "NEFT"/,
  ],
  'a UPI payment loses its channel': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R1').channel = null),
    /payment R1 has channel null for the line "UPI\/R1", which says "UPI"/,
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
  'a payment gets a note of the user\'s own': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R6').user_note = 'x'),
    /transactions\[R6\]\.user_note/,
  ],
  'a payment is said to have a note of the user\'s': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R6').note_is_yours = true),
    /transactions\[R6\]\.note_is_yours/,
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
  'a top payee is called something no payment is': [
    (a) => (a.figures.months['2026-09'].topPayees[0].name = 'Big Basket'),
    /2026-09: the top payee "Big Basket" is not what any payment is called/,
  ],
  'a top payee changes its total': [
    (a) => (a.figures.months['2026-09'].topPayees[0].total = 101),
    /months\.2026-09\.topPayees\[BigBasket\]\.total/,
  ],
  'a top payee goes missing': [
    (a) => (a.figures.months['2026-09'].topPayees = []),
    /2026-09: 1 top payees before and 0 after/,
  ],
  'a recurring charge goes missing': [
    (a) => (a.figures.months['2026-09'].recurring = []),
    /2026-09: 1 recurring rows before and 0 after/,
  ],
  // The accounts migration 041 makes. Each case damages one thing a split, a fold or a label can get wrong.
  'a bank splits into two accounts': [
    (a, b) => addStatement(a, b, 's2'), // same bank and number, an account of its own
    /statements of one account \(Bank ··0000\) were split across/,
  ],
  'a renamed statement is not folded into the account of its real bank': [
    // The old Rename typed text on October; the same number's other statement still names the bank.
    (a, b) => addStatement(a, b, 's2', { bank_name: 'Salary account' }),
    /statements of one account \(Salary account\) were split across/,
  ],
  'a statement ends up in no account': [
    (a) => {
      a.facts.statements[0].account_id = null
      a.facts.accounts = []
      a.facts.schema.tableRows.accounts = 0
    },
    /statement s belongs to account null, which is not in the ledger/,
  ],
  'two banks merge into one account': [
    (a, b) => {
      addStatement(a, b, 's2', { bank_name: 'hdfc', account_ref: 'XXXX1111' })
      a.facts.statements[1].account_id = 'acc_s'
      a.facts.accounts[0].statement_count = 2
    },
    /account acc_s holds statements of different accounts/,
  ],
  'the accounts table count is not the list': [
    (a) => (a.facts.schema.tableRows.accounts = 7),
    /the accounts table has 7 rows, the API lists 2 accounts/,
  ],
  'an account shows a balance its statements do not close at': [
    (a) => (a.facts.accounts[0].balance = 999),
    /account acc_s shows balance 999 as of 2026-08-02, its latest statement closes at 0 on 2026-08-02/,
  ],
  'an account shows a balance with no statement behind it': [
    (a) => (a.facts.accounts[1].balance = 5),
    /account acc_demo_s shows balance 5 as of null, its latest statement closes at null on null/,
  ],
  'an account is labelled by the parser key': [
    (a) => {
      a.facts.accounts[0].account_label = 'generic ··0000'
      a.facts.statements[0].account_label = 'generic ··0000'
    },
    /account acc_s is labelled "generic ··0000", expected "Bank ··0000"/,
  ],
  'an account is labelled by a bare lower-case key': [
    (a) => {
      a.facts.accounts[0].account_label = 'generic'
      a.facts.statements[0].account_label = 'generic'
    },
    /account acc_s is labelled by a raw key, "generic"/,
  ],
  'an account takes a name nobody typed': [
    (a) => {
      a.facts.accounts[0].name = 'Oops'
      a.facts.accounts[0].account_label = 'Oops'
      a.facts.statements[0].account_label = 'Oops'
    },
    /account acc_s is named "Oops", expected null/,
  ],
  'an account loses the name that was typed': [
    (a, b) => {
      b.facts.statements[0].bank_name = 'Salary account'
      a.facts.statements[0].bank_name = 'Salary account'
    },
    /account acc_s is named null, expected "Salary account"/,
  ],
  'a printed period is invented for an old statement': [
    (a) => {
      a.facts.statements[0].printed_start = '2026-08-01'
      a.facts.statements[0].printed_end = '2026-08-31'
    },
    /statements\[s\]\.printed_start/,
  ],
  'the statement list keeps the whole account number': [
    (a) => (a.facts.statements[0].account_ref = 'XXXX0000'),
    /statement s still carries its whole account number|statements\[s\]\.account_ref/,
  ],
  'the last four digits are the wrong ones': [
    (a) => (a.facts.statements[0].account_ref_last4 = '9999'),
    /statement s shows last four digits "9999", its number ends in "0000"/,
  ],
  'the account ends in the wrong digits': [
    (a) => (a.facts.accounts[0].account_ref_last4 = '1234'),
    /account acc_s ends in 1234, its statements' number ends in 0000/,
  ],
  'the account sits at another bank': [
    (a) => (a.facts.accounts[0].bank_name = 'hdfc'),
    /account acc_s is at bank "hdfc", its statements say "generic"/,
  ],
  'a statement says its file held more payments than it has': [
    (a) => (a.facts.statements[0].rows_in_file = 20),
    /statement s says its file held 20 payments, it has 13/,
  ],
  'a statement says it added fewer payments than it has': [
    (a) => (a.facts.statements[0].rows_added = 5),
    /statement s says it added 5 payments, it has 13/,
  ],
  'a stored count is never filled in': [
    (a) => (a.facts.statements[1].rows_in_file = null),
    /statement demo_s says its file held null payments, it has 0/,
  ],
  'the stored balance check disagrees with the one shown': [
    (a) => (a.facts.statements[0].balance_checked = 3),
    /statement s stores a balance check of 12 of 3, the list shows 12 of 12/,
  ],
  'a balanced statement is flagged after the upgrade': [
    (a) => {
      a.facts.statements[0].balance_check = { checked: 12, consistent: 11, balanced: false }
      a.facts.statements[0].balance_consistent = 11
    },
    /statement s was 12 of 12 \(balanced true\) and is now 11 of 12 \(balanced false\)/,
  ],
  'a statement that was not balanced is called balanced after the upgrade': [
    (a, b) => {
      b.facts.statements[0].balance_check = { checked: 12, consistent: 10, balanced: false }
      a.facts.statements[0].balance_consistent = 10
      a.facts.statements[0].balance_check = { checked: 12, consistent: 10, balanced: true }
    },
    /statement s is called balanced true at 10 of 12/,
  ],
  'a statement shares payments with another on an upgrade': [
    (a) => (a.facts.statements[0].shared_with_others = 2),
    /statements\[s\]\.shared_with_others/,
  ],
  'a statement offers a month its payments are not in': [
    (a) => (a.facts.statements[0].payment_months = ['2026-08', '2026-09']),
    /statement s says its payments are in \["2026-08","2026-09"\], they are in \["2026-08"\]/,
  ],
  'a statement gets the wrong last day for its payments': [
    (a) => (a.facts.statements[0].last_payment = '2026-08-30'),
    /statement s says its payments run 2026-08-02 to 2026-08-30, they run 2026-08-02 to 2026-08-02/,
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
  // Migration 044 and pairing. Each case damages one thing the rebuild or a pairing can get wrong.
  'the upgrade pairs two payments': [
    (a) => {
      a.facts.transactions.find((t) => t.id === 'R1').transfer = { transaction_id: 'R2', direction: 'to', account_label: 'SBI savings', txn_date: '2026-08-02', text: 'Moved to SBI savings' }
    },
    /payment R1 is paired as a transfer after the upgrade/,
  ],
  'the upgrade holds a payment back as waiting on a pair': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R1').waiting_on_pair = true),
    /transactions\[R1\]\.waiting_on_pair/,
  ],
  'the upgrade labels a payment as paired': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R1').source = 'pair'),
    /payment R1 carries a pair label after the upgrade|payment R1's label changed in the upgrade: source was "manual", is "pair"/,
  ],
  'the rebuild drops a label': [
    (a) => {
      Object.assign(a.facts.transactions.find((t) => t.id === 'R6'), { annotation_id: null, category: null, subcategory: null, merchant: null, tags: [], confidence: null, source: null, original_source: null })
      a.facts.annotationRows = a.facts.annotationRows.filter((r) => r.transaction_id !== 'R6')
    },
    /(payment R6's label changed in the upgrade: annotation_id|\d+ labels after the upgrade, \d+ before)/,
  ],
  'the rebuild loses a label but keeps the payment labelled': [
    (a) => a.facts.annotationRows.pop(),
    /\d+ labels after the upgrade, \d+ before/,
  ],
  'the rebuild loses a typed merchant name in the label table': [
    (a) => (a.facts.annotationRows.find((r) => r.transaction_id === 'R5').merchant = null),
    /label of payment R5 changed in the upgrade: column merchant was /,
  ],
  'the rebuild stamps a new time on a label': [
    (a) => (a.facts.annotationRows.find((r) => r.transaction_id === 'R6').annotated_at = '2026-11-18 09:00:00'),
    /label of payment R6 changed in the upgrade: column annotated_at was "2026-08-02 10:00:00"/,
  ],
  'the rebuild drops the reasoning of a label': [
    (a) => (a.facts.annotationRows.find((r) => r.transaction_id === 'R4').reasoning = null),
    /label of payment R4 changed in the upgrade: column reasoning/,
  ],
  'the rebuild changes a label id': [
    (a) => (a.facts.annotationRows.find((r) => r.transaction_id === 'R6').id = 'a_other'),
    /label of payment R6 changed in the upgrade: column id was "a_R6"/,
  ],
  'the rebuild moves a category id that was filled': [
    (a, b) => {
      b.facts.annotationRows.find((r) => r.transaction_id === 'R6').category_id = 'cat_shop'
      a.facts.annotationRows.find((r) => r.transaction_id === 'R6').category_id = 'cat_food'
    },
    /label of payment R6 changed in the upgrade: column category_id was "cat_shop", is "cat_food"/,
  ],
  'the rebuild changes a confidence': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R6').confidence = 0.5),
    /payment R6's label changed in the upgrade: confidence was 1, is 0.5/,
  ],
  'the rebuild changes where a label came from': [
    (a) => (a.facts.transactions.find((t) => t.id === 'R6').source = 'llm'),
    /payment R6's label changed in the upgrade: source was "manual", is "llm"/,
  ],
  'the rebuild loses a tag': [
    (a, b) => (b.facts.transactions.find((t) => t.id === 'R6').tags = ['gift']),
    /payment R6's label changed in the upgrade: tags was \["gift"\], is \[\]/,
  ],
  'the unpaired table starts with a remembered pair': [
    (a) => (a.facts.schema.tableRows.transfer_unpaired = 1),
    /1 unpaired transfers are remembered after the upgrade|schema\.tableRows\.transfer_unpaired/,
  ],
  'the unpaired table is not there': [
    (a) => (a.facts.schema.tableRows.transfer_unpaired = null),
    /"stored-transfer-unpaired-table" \(stored\) did not happen/,
  ],
  'the layouts table starts with a remembered layout': [
    (a) => (a.facts.schema.tableRows.import_layouts = 1),
    /1 remembered spreadsheet layouts after the upgrade|schema\.tableRows\.import_layouts/,
  ],
  'the layouts table is not there': [
    (a) => (a.facts.schema.tableRows.import_layouts = null),
    /"stored-import-layouts-table" \(stored\) did not happen/,
  ],
  'the layouts migration does not run': [
    (a) => (a.facts.schema.appliedMigrations = a.facts.schema.appliedMigrations.filter((m) => !m.startsWith('045'))),
    /"stored-migration-045" \(stored\) did not happen/,
  ],
  'the pairing migration does not run': [
    (a) => (a.facts.schema.appliedMigrations = a.facts.schema.appliedMigrations.filter((m) => !m.startsWith('044'))),
    /"stored-migration-044" \(stored\) did not happen/,
  ],
  'money not sorted yet is still counted as a refund': [
    (a) => {
      a.figures.months['2026-11'].otherIn = 85450
      a.figures.months['2026-11'].unsortedIn = 85450
    },
    /2026-11: refunds and paybacks went from 85450 to 85450 but 85450 is not sorted yet/,
  ],
  'refunds and paybacks lose more than the unsorted money': [
    (a) => (a.figures.months['2026-10'].otherIn = 2000),
    /2026-10: refunds and paybacks went from 3361 to 2000 but 750 is not sorted yet/,
  ],
  'refunds and paybacks change in a month with nothing unsorted': [
    (a) => (a.figures.months['2026-09'].otherIn = 10),
    /months\.2026-09\.otherIn/,
  ],
  'the unsorted money in is less than zero': [
    (a) => (a.figures.months['2026-08'].unsortedIn = -5),
    /2026-08: -5 of money in is not sorted yet/,
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
    damage(after, before)
    const problems = compare(before, after)
    assert.ok(problems.length > 0, 'the comparison passed a damaged upgrade')
    assert.ok(problems.some((p) => message.test(p)), `no problem matches ${message}:\n${problems.join('\n')}`)
  })
}

// The verdict of J15 is about a ledger made on a fixed day, never about the day the journey runs.
// An entry that may or may not happen, or that names the month of the run, is how a date drift got
// patched in before; neither is allowed back.
test('no entry depends on the day the journey runs', () => {
  for (const entry of expected.changes) {
    assert.ok(!entry.optional, `${entry.id} is optional: an entry either happens on the journey's ledger or is stale`)
    for (const path of [entry.path].flat()) assert.ok(!path.includes('{month}'), `${entry.id} names the month of the run`)
  }
})
