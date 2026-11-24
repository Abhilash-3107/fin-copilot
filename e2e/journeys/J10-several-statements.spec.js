// J10 - Several statements in one month. Three files are dropped together: two accounts
// at two different banks, and a second export of the first that overlaps it. They land in
// their accounts, the overlap adds only what is new, the transfer between the two accounts
// is paired and counted in no In or Out figure, the books show both accounts and the month,
// and the account filter shows each account's figures, which add up to All accounts.
import { readFileSync } from 'node:fs'
import { test, expect } from '../fixtures.js'
import { figureNear, open } from '../lib/ui.js'
import { FIXTURE_PDF_TRANSFER_IN, FIXTURE_PDF_TRANSFER_OUT, FIXTURE_PDF_TRANSFER_OUT_LATE } from '../lib/statement.js'

const upload = (name, path) => ({ name, mimeType: 'application/pdf', buffer: readFileSync(path) })
const fileRow = (page, name) => page.getByRole('listitem').filter({ hasText: name })
const summaryOf = (page) => page.getByRole('status').filter({ hasText: 'Finished:' })

const FIRST = 'ICICI ··7788'
const SECOND = 'Axis ··1234'

// No model: what pairing does not settle is left unsorted, the same for every figure.
test.use({ modelMode: 'none' })

test.describe('J10 several statements in one month', () => {
  test.beforeEach(async ({ page }) => {
    await open(page, '/upload')
    await page.locator('input[type=file]').setInputFiles([
      upload('first-account-october.pdf', FIXTURE_PDF_TRANSFER_OUT),
      upload('second-account-october.pdf', FIXTURE_PDF_TRANSFER_IN),
      upload('first-account-late-october.pdf', FIXTURE_PDF_TRANSFER_OUT_LATE),
    ])
    await page.getByRole('button', { name: /^Add (\d+ statements|statement)$/ }).click()
    await expect(summaryOf(page)).toBeVisible()
  })

  const everything = (app, account = '') => app.api.get(`/transactions?include=annotation&limit=1000${account ? `&account_id=${account}` : ''}`)

  test('the three files land in two accounts, and the overlapping one adds only the payment that is new', async ({ page, app }) => {
    await expect(fileRow(page, 'first-account-october.pdf')).toContainText(`Added to a new account, ${FIRST}.`)
    await expect(fileRow(page, 'second-account-october.pdf')).toContainText(`Added to a new account, ${SECOND}.`)
    // The third file is the first account's again: no new account, and it says what it brought.
    const late = fileRow(page, 'first-account-late-october.pdf')
    await expect(late).toContainText(`Added to ${FIRST}`)
    await expect(late).not.toContainText('Added to a new account')
    await expect(late).toContainText('14 Oct to 31 Oct - 1 new payment, 3 already in your ledger - Balance checks out')

    const accounts = await app.api.get('/accounts')
    expect(accounts.map((a) => [a.account_label, a.statement_count]).sort()).toEqual([[SECOND, 1], [FIRST, 2]].sort())
    const statements = await app.api.get('/statements')
    expect(statements).toHaveLength(3)
    const overlap = statements.find((s) => s.rows_in_file === 4)
    expect([overlap.rows_added, overlap.txn_count]).toEqual([1, 1])
    expect(overlap.balance_check.balanced).toBe(true)

    // 6 + 5 + 1 payments, and none of them twice.
    const all = await everything(app)
    expect(all).toHaveLength(12)
    const keys = all.map((t) => `${t.account_id} ${t.txn_date} ${t.amount} ${t.debit_credit} ${t.raw_description}`)
    expect(new Set(keys).size, 'a payment is in the ledger twice').toBe(keys.length)
    expect(all.filter((t) => t.amount === 12000 && t.debit_credit === 'debit'), 'the transfer out, once').toHaveLength(1)
    const first = accounts.find((a) => a.account_label === FIRST)
    expect(await everything(app, first.id)).toHaveLength(7)
  })

  test('the transfer between the two accounts is paired once and counted in no In or Out figure', async ({ app }) => {
    const all = await everything(app)
    const out = all.find((t) => t.debit_credit === 'debit' && t.amount === 12000)
    const inn = all.find((t) => t.debit_credit === 'credit' && t.amount === 12000)
    expect(out.account_label).toBe(FIRST)
    expect(inn.account_label).toBe(SECOND)
    expect(out.transfer).toMatchObject({ direction: 'to', account_label: SECOND })
    expect(inn.transfer).toMatchObject({ direction: 'from', account_label: FIRST })
    expect([out.category, out.source, inn.category, inn.source]).toEqual(['Self Transfers', 'pair', 'Self Transfers', 'pair'])

    // In and Out are the other payments' credits and debits, for all accounts and for each one.
    const view = async (account = '') => {
      const rows = await everything(app, account)
      const { verdict } = await app.api.get(`/insights?month=2026-10${account ? `&account_id=${account}` : ''}`)
      const out = rows.filter((t) => t.debit_credit === 'debit' && !['Self Transfers', 'Investments'].includes(t.category))
      const earned = rows.filter((t) => t.debit_credit === 'credit' && t.category !== 'Self Transfers')
      return { verdict, spent: out.reduce((s, t) => s + t.amount, 0), earned: earned.reduce((s, t) => s + t.amount, 0), rows }
    }
    const everyone = await view()
    expect(everyone.verdict.money_out).toBeCloseTo(everyone.spent, 2)
    expect(everyone.verdict.money_in).toBeCloseTo(everyone.earned, 2)
    // Counting the transfer would add it to both sides.
    expect(everyone.verdict.money_out).toBeLessThan(everyone.rows.filter((t) => t.debit_credit === 'debit').reduce((s, t) => s + t.amount, 0))

    // Each account's figures, and that they add up to All accounts: a pair is one account's
    // Out and the other's In, and neither counts, so nothing is lost or counted twice.
    const accounts = await app.api.get('/accounts')
    const parts = []
    for (const account of accounts) {
      const one = await view(account.id)
      expect(one.verdict.money_out).toBeCloseTo(one.spent, 2)
      expect(one.verdict.money_in).toBeCloseTo(one.earned, 2)
      parts.push(one)
    }
    expect(parts.reduce((s, p) => s + p.verdict.money_out, 0)).toBeCloseTo(everyone.verdict.money_out, 2)
    expect(parts.reduce((s, p) => s + p.verdict.money_in, 0)).toBeCloseTo(everyone.verdict.money_in, 2)
    expect(parts.reduce((s, p) => s + p.verdict.invested, 0)).toBeCloseTo(everyone.verdict.invested, 2)
    expect(parts.reduce((s, p) => s + p.verdict.kept, 0)).toBeCloseTo(everyone.verdict.kept, 2)
  })

  test('the books show both accounts and the month, and list the move between them as not counted', async ({ page }) => {
    await expect(page.getByRole('rowheader', { name: new RegExp(FIRST) })).toBeVisible()
    await expect(page.getByRole('rowheader', { name: new RegExp(SECOND) })).toBeVisible()
    const grid = page.getByRole('region', { name: 'Your books' })
    await expect(grid.getByRole('columnheader')).toHaveText(['Account', 'Oct 2026'])
    // One cell for each account, and the first account's cell holds its two statements.
    const first = grid.getByRole('link', { name: new RegExp(`^${FIRST}, October 2026, `) })
    const second = grid.getByRole('link', { name: new RegExp(`^${SECOND}, October 2026, `) })
    await expect(first).toHaveCount(1)
    await expect(second).toHaveCount(1)
    await expect(first).toHaveAttribute('aria-label', /, 7 payments, /)
    await expect(second).toHaveAttribute('aria-label', /, 5 payments, /)
    await expect(grid.getByRole('img', { name: /no statement/ })).toHaveCount(0)
    await expect(page.getByText(new RegExp(`Moved ₹12,000 from ${FIRST} to ${SECOND} on 14 Oct, not counted`))).toBeVisible()
  })

  test('the account filter shows each account, and the lists add up to all of them', async ({ page, app }) => {
    await open(page, '/transactions')
    const filter = page.getByRole('combobox', { name: 'Account' })
    await expect(filter.locator('option')).toHaveText(['All accounts', FIRST, SECOND])
    const summary = page.getByText(/^\d+ payments? · /).first()

    /** The one-line summary of the list as the page shows it: count, out, in. */
    const read = async () => {
      const text = await summary.innerText()
      const [, count, out, inn] = text.match(/^(\d+) payments? · ₹([\d,.]+) out · ₹([\d,.]+) in/)
      return { count: Number(count), out: Number(out.replaceAll(',', '')), in: Number(inn.replaceAll(',', '')) }
    }
    const seen = {}
    for (const [label, expectedCount] of [['All accounts', 12], [FIRST, 7], [SECOND, 5]]) {
      await filter.selectOption({ label })
      await expect(summary).toContainText(`${expectedCount} payments`)
      seen[label] = await read()
    }
    expect(seen[FIRST].count + seen[SECOND].count).toBe(seen['All accounts'].count)
    expect(seen[FIRST].out + seen[SECOND].out).toBeCloseTo(seen['All accounts'].out, 2)
    expect(seen[FIRST].in + seen[SECOND].in).toBeCloseTo(seen['All accounts'].in, 2)

    // Both sides of the pair say so in words, whichever account is chosen.
    await filter.selectOption({ label: 'All accounts' })
    await expect(page.getByRole('row').filter({ hasText: `Moved to ${SECOND}` })).toHaveCount(1)
    await expect(page.getByRole('row').filter({ hasText: `Moved from ${FIRST}` })).toHaveCount(1)

    // The Money Map for each account, and for all of them, says what the server counts.
    await open(page, '/insights')
    const accounts = await app.api.get('/accounts')
    const scopes = [['All accounts', ''], ...accounts.map((a) => [a.account_label, a.id])]
    for (const [label, id] of scopes) {
      await page.getByRole('combobox', { name: 'Account' }).selectOption({ label })
      const { verdict } = await app.api.get(`/insights?month=2026-10${id ? `&account_id=${id}` : ''}`)
      // The screen rounds to the rupee.
      const near = (figure, value) => Math.abs(figure - value) <= 0.5
      await expect.poll(async () => near(await figureNear(page, 'Out'), verdict.money_out), { message: `Out for ${label}` }).toBe(true)
      expect(near(await figureNear(page, 'In'), verdict.money_in), `In for ${label}`).toBe(true)
      expect(near(await figureNear(page, 'Kept'), verdict.kept), `Kept for ${label}`).toBe(true)
    }
  })

  test('money in that is not sorted yet is not called a refund or payback', async ({ page, app }) => {
    const { verdict } = await app.api.get('/insights?month=2026-10')
    expect(verdict.unsorted_in).toBe(7000)
    expect(verdict.other_in).toBe(0)
    // Without a model nothing else in the files is sorted into a refund either: all of In
    // is either earned or not sorted yet.
    expect(verdict.money_in).toBeCloseTo(verdict.earned + verdict.unsorted_in, 2)

    for (const route of ['/dashboard', '/insights']) {
      await open(page, route)
      await expect(page.getByText(/incl ₹7,000 not sorted yet/)).toBeVisible()
      await expect(page.getByText(/refunds and paybacks/), `${route} calls unsorted money a refund`).toHaveCount(0)
    }
  })
})