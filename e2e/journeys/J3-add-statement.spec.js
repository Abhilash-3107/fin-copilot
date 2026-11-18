// J3 - Add statements. Drop the fixture statements, see the balance check pass,
// see sorting start by itself, and follow the way on to Teach Me.
import { readFileSync } from 'node:fs'
import { test, expect } from '../fixtures.js'
import { open } from '../lib/ui.js'
import { FIXTURE_PDF, FIXTURE_PDF_NOV, FIXTURE_PDF_OTHER_BANK, FIXTURE_PDF_OVERLAP } from '../lib/statement.js'

async function drop(page, files) {
  await open(page, '/upload')
  await page.locator('input[type=file]').setInputFiles(files)
  await page.getByRole('button', { name: /^Add (\d+ statements|statement)$/ }).click()
}

const asUpload = (name, path) => ({ name, mimeType: 'application/pdf', buffer: readFileSync(path) })
const fileRow = (page, name) => page.getByRole('listitem').filter({ hasText: name })
// The toast live region is also a status, so pick the drop's own summary.
const summaryOf = (page) => page.getByRole('status').filter({ hasText: 'Finished:' })
// The fixture header has no bank name, so its account reads "Bank" and the last digits of its number.
const statementRows = (page) => page.getByRole('row').filter({ hasText: 'Bank ··0000' })

test.describe('J3 add a statement', () => {
  test('the page says what it supports before the first upload', async ({ page }) => {
    await open(page, '/upload')
    await expect(page.getByText(/Works with Kotak statements/)).toBeVisible()
    await expect(page.getByText(/running-balance column/)).toBeVisible()
    await expect(page.getByText(/Password-protected PDFs work/)).toBeVisible()
    await expect(page.getByText(
      /Your statement stays on this computer\. If you connect an online AI model in Settings, each payment.s date, amount, description and note are sent to it for sorting, along with a few similar payments you have already sorted and how you sorted them\./,
    )).toBeVisible()
    await expect(page.getByText(/sorting starts by itself/)).toBeVisible()
  })

  test('an uploaded statement shows its balance check passing', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const item = fileRow(page, 'sample_statement.pdf')
    await expect(item).toContainText('8 payments added')
    await expect(item).toContainText('Balance checks out')

    const [statement] = await app.api.get('/statements')
    expect(statement.balance_check.balanced).toBe(true)
    expect(statement.balance_check.checked).toBeGreaterThan(0)
    const { consistent, checked } = statement.balance_check
    await expect(statementRows(page)).toContainText(`${consistent}/${checked}`)
  })

  test('a statement row keeps Sort again and a menu, and none of the memory controls', async ({ page }) => {
    await drop(page, asUpload('sample_statement.pdf', FIXTURE_PDF))
    await expect(summaryOf(page)).toBeVisible()
    const row = statementRows(page).first()
    await expect(row.getByRole('button', { name: /Sort again/ })).toBeVisible()
    await expect(page.getByText(/memory|rebuild/i)).toHaveCount(0)
    await row.getByRole('button', { name: /^More for / }).click()
    await expect(page.getByRole('menuitem')).toHaveText(['Name this account', 'Start over', 'Delete'])
    // The keys work: Escape closes the menu and puts focus back on its button.
    await page.keyboard.press('Escape')
    await expect(page.getByRole('menu')).toHaveCount(0)
    await expect(row.getByRole('button', { name: /^More for / })).toBeFocused()
  })

  test('sorting starts by itself when the statement lands', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    // No click on anything after Upload.
    await expect(summaryOf(page)).toContainText(/Finished: \d+ sorted/)

    // The stub, not some other model, did the work.
    const chats = app.stub.requests.filter((r) => r.path === '/api/chat')
    expect(chats.length, 'chat calls the model stub received').toBeGreaterThan(0)
    const [job] = await app.api.get('/annotations/jobs')
    expect(job.status).toBe('completed')
    expect(job.result.ai_active, 'the run reports the model as active').toBe(true)

    // The table counts "sorted" the way Teach Me does: what it is not waiting on.
    const [statement] = await app.api.get('/statements')
    const queue = await app.api.get('/annotations/review-queue')
    expect(statement.sorted_count + queue.length).toBe(statement.txn_count)
    await expect(statementRows(page)).toContainText(`${statement.sorted_count}/${statement.txn_count}`)
  })

  test('the page says what happened and leads to Teach Me, where the rest waits', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const summary = summaryOf(page)
    await expect(summary).toContainText(/Finished: \d+ sorted, \d+ needs? you/)

    // Whatever the model was not sure about waits for the user.
    const queue = await app.api.get('/annotations/review-queue')
    expect(queue.length, 'rows waiting in the queue').toBeGreaterThan(0)
    await summary.getByRole('link', { name: /Teach Me/ }).click()
    await expect(page.getByText(new RegExp(`0\\s*/\\s*${queue.length}\\b`))).toBeVisible()
  })

  // One meaning of "sorted": the summary, the Sorted column and the Teach Me queue
  // must give the same numbers, even where a job's own counters would not.
  async function expectOneCount(page, app) {
    const [statement] = await app.api.get('/statements')
    const queue = await app.api.get('/annotations/review-queue')
    const needYou = statement.txn_count - statement.sorted_count
    const words = `${statement.sorted_count} sorted, ${needYou} ${needYou === 1 ? 'needs' : 'need'} you`
    await expect(summaryOf(page)).toContainText(`Finished: ${words}`)
    await expect(fileRow(page, 'sample_statement.pdf')).toContainText(words)
    await expect(statementRows(page)).toContainText(`${statement.sorted_count}/${statement.txn_count}`)
    expect(queue.length, 'payments waiting in Teach Me').toBe(needYou)
    return statement
  }

  test('the summary, the table and Teach Me agree when the model is sure a payment is Uncategorized', async ({ page, app }) => {
    // A bar low enough that the model's 0.85 passes it: only "Uncategorized" keeps those payments in the queue.
    await app.api.put('/config', { confidence_threshold: 0.5 })
    app.stub.answerUncategorized(/ELECTRICITY|BMRCL/i)
    await drop(page, FIXTURE_PDF)
    await expect(summaryOf(page)).toContainText(/Finished: \d+ sorted/)
    const rows = await app.api.get('/transactions?include=annotation&limit=1000')
    const confidentUncategorized = rows.filter((t) => t.category === 'Uncategorized' && t.confidence >= 0.5)
    expect(confidentUncategorized.length, 'payments the model sorted into Uncategorized').toBeGreaterThan(0)
    await expectOneCount(page, app)
  })

  test('the summary, the table and Teach Me agree after the review bar is lowered', async ({ page, app }) => {
    // The model's answers (0.81) clear 0.5 but not the config default of 0.85 a job's own counters used.
    await app.api.put('/config', { confidence_threshold: 0.5 })
    await drop(page, FIXTURE_PDF)
    await expect(summaryOf(page)).toContainText(/Finished: \d+ sorted/)
    const statement = await expectOneCount(page, app)
    expect(statement.sorted_count, 'sorted at the lowered bar').toBeGreaterThanOrEqual(5)
  })

  test('the summary, the table and Teach Me agree after the review bar is raised', async ({ page, app }) => {
    await app.api.put('/config', { confidence_threshold: 0.99 })
    await drop(page, FIXTURE_PDF)
    await expect(summaryOf(page)).toContainText(/Finished: \d+ sorted/)
    await expectOneCount(page, app)
  })

  test('two statements dropped together go in one after the other, each with its own result', async ({ page, app }) => {
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_NOV])

    for (const name of ['sample_statement.pdf', 'sample_statement_nov.pdf']) {
      const item = fileRow(page, name)
      await expect(item).toContainText('8 payments added')
      await expect(item).toContainText(/\d+ sorted, \d+ needs? you/)
    }
    await expect(statementRows(page)).toHaveCount(2)
    await expect(summaryOf(page)).toContainText(/Finished: \d+ sorted/)

    // Each statement was sorted by a job of its own, one at a time.
    const statements = await app.api.get('/statements')
    const jobs = await app.api.get('/annotations/jobs')
    for (const statement of statements) {
      const job = jobs.find((j) => j.statement_id === statement.id)
      expect(job?.status, `the job for ${statement.statement_month}`).toBe('completed')
      // "Sorted" in the table is what Teach Me is not waiting on.
      expect(statement.sorted_count).toBeLessThanOrEqual(statement.txn_count - job.result.llm_failed)
    }
  })

  test('a statement dropped while another is being sorted waits its turn, and then runs', async ({ page, app }) => {
    app.stub.hold()
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_NOV])
    const first = fileRow(page, 'sample_statement.pdf')
    const second = fileRow(page, 'sample_statement_nov.pdf')

    // The first job is stuck on the model; the second waits in line behind it.
    await expect(second).toContainText('Waiting to sort')
    await expect(first).toContainText(/Sorting \d+ of \d+|Sorting/)
    const active = await app.api.get('/annotations/jobs?active=1')
    expect(active.map((j) => j.status)).toEqual(['running', 'queued'])

    // The second statement is still waiting, not lost, once the first is released.
    app.stub.release()
    await expect(summaryOf(page)).toContainText(/Finished: \d+ sorted/)
    const jobs = (await app.api.get('/annotations/jobs')).reverse()
    expect(jobs.map((j) => j.status)).toEqual(['completed', 'completed'])
    // Oldest statement first, each exactly once.
    const statements = await app.api.get('/statements')
    const month = (id) => statements.find((s) => s.id === id).statement_month
    expect(jobs.map((j) => month(j.statement_id))).toEqual(['2026-10', '2026-11'])
  })

  test('a file that is not a PDF is refused up front and the PDF beside it still goes in', async ({ page, app }) => {
    await open(page, '/upload')
    await page.locator('input[type=file]').setInputFiles([
      { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not a statement') },
      asUpload('sample_statement.pdf', FIXTURE_PDF),
    ])
    await expect(page.getByRole('alert').filter({ hasText: 'notes.txt' })).toContainText("notes.txt isn't a PDF")
    await expect(fileRow(page, 'notes.txt')).toHaveCount(0)

    await page.getByRole('button', { name: /^Add (\d+ statements|statement)$/ }).click()
    await expect(fileRow(page, 'sample_statement.pdf')).toContainText('8 payments added')
    expect(await app.api.get('/statements')).toHaveLength(1)
  })

  test('one bad file does not lose the others', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    // A PDF the parser cannot read: right bytes at the front, no table inside.
    const blank = { name: 'blank.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n') }
    await open(page, '/upload')
    await page.locator('input[type=file]').setInputFiles([blank, asUpload('sample_statement.pdf', FIXTURE_PDF)])
    await page.getByRole('button', { name: /^Add (\d+ statements|statement)$/ }).click()

    await expect(fileRow(page, 'sample_statement.pdf')).toContainText('8 payments added')
    await expect(fileRow(page, 'blank.pdf')).toContainText("no table of payments that Finance Copilot can read")
    expect(await app.api.get('/statements')).toHaveLength(1)
  })
})

test.describe('J3 with no model connected', () => {
  test.use({ modelMode: 'none' })

  test('the statement is still sorted by rules and the rest is left for you, with the way to connect a model', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const summary = summaryOf(page)
    await expect(summary).toContainText(/Finished: \d+ sorted, [1-9]\d* needs? you/)
    await expect(summary).toContainText('No model is connected')
    await expect(summary.getByRole('link', { name: /Settings › AI & Model/ })).toBeVisible()

    const unsorted = await app.api.get('/transactions?unannotated=true')
    expect(unsorted.length, 'rows nobody decided').toBeGreaterThan(0)
    await summary.getByRole('link', { name: /Teach Me/ }).click()
    await expect(page.getByText(new RegExp(`0\\s*/\\s*\\d+`))).toBeVisible()
  })
})

test.describe('J3 accounts', () => {
  const accountRows = (page) => page.getByRole('row').filter({ has: page.getByRole('button', { name: /^More for / }) })
  const picker = (page) => page.getByRole('combobox').first()

  test('a statement lands in a new account that can be named on its row, and the name follows it to Transactions', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const row = fileRow(page, 'sample_statement.pdf')
    await expect(row).toContainText('Added to a new account, Bank ··0000.')

    await row.getByRole('button', { name: 'Name it' }).click()
    const name = row.getByRole('textbox', { name: 'Name this account' })
    await expect(name).toBeFocused()
    await name.fill('Salary account')
    await name.press('Enter')
    await expect(row).toContainText('Added to a new account, Salary account.')
    await expect(row.getByRole('button', { name: 'Name it' })).toHaveCount(0)
    // Focus lands on the line that now says the name, not on the page body.
    await expect(row.getByText('Added to a new account, Salary account.')).toBeFocused()

    // The list says it too, and so does the server.
    await expect(page.getByRole('row').filter({ hasText: 'Salary account' })).toHaveCount(1)
    const [account] = await app.api.get('/accounts')
    expect(account).toMatchObject({ name: 'Salary account', account_label: 'Salary account', account_ref_last4: '0000', statement_count: 1 })
    expect(JSON.stringify(account)).not.toContain('XXXX0000')

    // Move on inside the app, without a reload: the picker already has the new name.
    await page.getByRole('link', { name: 'Transactions' }).first().click()
    await expect(page.getByRole('button', { name: 'Sort again' })).toBeVisible()
    await expect(picker(page).locator('option')).toHaveText(['All statements', 'Salary account - 1 Oct to 8 Oct'])
  })

  test('naming is cancelled with Escape and saves nothing', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const row = fileRow(page, 'sample_statement.pdf')
    await row.getByRole('button', { name: 'Name it' }).click()
    const name = row.getByRole('textbox', { name: 'Name this account' })
    await name.fill('Never saved')
    await name.press('Escape')
    await expect(name).toHaveCount(0)
    await expect(row).toContainText('Added to a new account, Bank ··0000.')
    await expect(row.getByRole('button', { name: 'Name it' })).toBeFocused()
    const [account] = await app.api.get('/accounts')
    expect(account.name).toBeNull()
  })

  test('a blank name leaves Name it on offer, with focus on the line', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const row = fileRow(page, 'sample_statement.pdf')
    await row.getByRole('button', { name: 'Name it' }).click()
    const name = row.getByRole('textbox', { name: 'Name this account' })
    await name.fill('   ')
    await name.press('Enter')
    await expect(name).toHaveCount(0)
    await expect(row.getByRole('button', { name: 'Name it' })).toBeVisible()
    await expect(row.getByText('Added to a new account, Bank ··0000.')).toBeFocused()
    expect((await app.api.get('/accounts'))[0].name).toBeNull()
  })

  test('a statement is split out of its account into a new one, and the dialog keeps Tab inside', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    await expect(summaryOf(page)).toContainText('Finished:')
    await drop(page, FIXTURE_PDF_NOV)
    await expect(fileRow(page, 'sample_statement_nov.pdf')).toContainText('Added to Bank ··0000')
    expect(await app.api.get('/accounts')).toHaveLength(1)

    await page.getByRole('button', { name: 'More for Bank ··0000 - 1 Nov to 8 Nov' }).click()
    await page.getByRole('menuitem', { name: 'Move to account…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Move to account' })
    await expect(dialog.getByText('A new account', { exact: true })).toBeVisible()

    // Tab and Shift+Tab never leave the dialog, however long the lap.
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab')
      expect(await dialog.evaluate((el) => el.contains(document.activeElement))).toBe(true)
    }
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Shift+Tab')
      expect(await dialog.evaluate((el) => el.contains(document.activeElement))).toBe(true)
    }

    // A new account needs a name: the button waits, and Enter in the empty field does nothing.
    const nameField = dialog.getByRole('textbox', { name: 'Name for the new account' })
    await expect(dialog.getByRole('button', { name: 'Move to a new account' })).toBeDisabled()
    await expect(dialog).toContainText('Give it a name, so you can tell the two accounts apart.')
    await nameField.fill('   ')
    await nameField.press('Enter')
    await expect(dialog).toBeVisible()
    expect(await app.api.get('/accounts')).toHaveLength(1)

    // Enter in the field moves the statement and does nothing else: no menu opens
    // on the button the dialog hands focus back to, and focus is on that button.
    await nameField.fill('Joint account')
    await expect(dialog.getByRole('button', { name: 'Move to a new account' })).toBeEnabled()
    await nameField.press('Enter')
    await expect(dialog).toHaveCount(0)
    await expect(accountRows(page).filter({ hasText: 'Joint account' })).toHaveCount(1)
    await expect(page.getByRole('menu')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'More for Joint account - 1 Nov to 8 Nov' })).toBeFocused()
    await expect(accountRows(page).filter({ hasText: 'Joint account' })).toHaveCount(1)
    const accounts = await app.api.get('/accounts')
    expect(accounts.map((a) => [a.account_label, a.statement_count]).sort()).toEqual([['Bank ··0000', 1], ['Joint account', 1]])
    // Both carry the same bank and number, which the ledger now lets stand.
    expect(accounts.map((a) => a.account_ref_last4)).toEqual(['0000', '0000'])
  })

  test('the next statement of a named account says so, and there is still one account', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    await fileRow(page, 'sample_statement.pdf').getByRole('button', { name: 'Name it' }).click()
    await page.getByRole('textbox', { name: 'Name this account' }).fill('Salary account')
    await page.keyboard.press('Enter')
    await expect(summaryOf(page)).toContainText('Finished:')

    await drop(page, FIXTURE_PDF_NOV)
    await expect(fileRow(page, 'sample_statement_nov.pdf')).toContainText('Added to Salary account')
    await expect(fileRow(page, 'sample_statement_nov.pdf').getByRole('button', { name: 'Name it' })).toHaveCount(0)
    expect(await app.api.get('/accounts')).toHaveLength(1)
    const list = page.getByText('Your accounts').locator('..')
    await expect(list).toContainText('Salary account')
    await expect(list).toContainText('2 statements')
    // The latest closing balance, with the day it is as of.
    await expect(list).toContainText(/₹[\d,]+ as of 8 Nov 2026/)
  })

  test('a statement from another bank is its own account, and can be moved between accounts', async ({ page, app }) => {
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_OTHER_BANK])
    await expect(summaryOf(page)).toContainText('Finished:')
    await expect(fileRow(page, 'sample_statement.pdf')).toContainText('Added to a new account, Bank ··0000.')
    await expect(fileRow(page, 'sample_statement_other_bank.pdf')).toContainText('Added to a new account, HDFC ··4321.')

    const accounts = await app.api.get('/accounts')
    expect(accounts.map((a) => a.account_label).sort()).toEqual(['Bank ··0000', 'HDFC ··4321'])
    const statements = await app.api.get('/statements')
    const other = statements.find((s) => s.account_label === 'HDFC ··4321')
    // The header prints the whole month; the payments stop on the 12th.
    expect([other.printed_start, other.printed_end]).toEqual(['2026-10-01', '2026-10-31'])
    expect([other.period_start, other.period_end]).toEqual(['2026-10-02', '2026-10-12'])

    // Move the other bank's statement into the first account: the emptied account goes away.
    await accountRows(page).filter({ hasText: 'HDFC ··4321' }).getByRole('button', { name: /^More for / }).click()
    await page.getByRole('menuitem', { name: 'Move to account…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Move to account' })
    await expect(dialog.getByRole('button', { name: 'HDFC ··4321' })).toHaveCount(0)
    await dialog.getByRole('button', { name: 'Bank ··0000' }).click()
    await expect(accountRows(page).filter({ hasText: 'HDFC ··4321' })).toHaveCount(0)
    await expect(accountRows(page).filter({ hasText: 'Bank ··0000' })).toHaveCount(2)
    expect((await app.api.get('/accounts')).map((a) => a.account_label)).toEqual(['Bank ··0000'])
  })
})

test.describe('J3 overlapping statements', () => {
  const accountRows = (page) => page.getByRole('row').filter({ has: page.getByRole('button', { name: /^More for / }) })
  const asPayments = (rows) => rows.map((t) => `${t.txn_date} ${t.amount} ${t.debit_credit} ${t.raw_description}`)

  test('a file that overlaps both months adds only what is new, and October shows no duplicate', async ({ page, app }) => {
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_NOV])
    await expect(summaryOf(page)).toContainText('Finished:')
    await drop(page, FIXTURE_PDF_OVERLAP)
    const row = fileRow(page, 'sample_statement_overlap.pdf')
    await expect(row).toContainText('5 Oct to 15 Nov - 3 new payments, 12 already in your ledger - Balance checks out')
    await expect(summaryOf(page)).toContainText('Finished:')

    const october = await app.api.get('/transactions?month=2026-10')
    const november = await app.api.get('/transactions?month=2026-11')
    expect(october).toHaveLength(8)
    expect(november).toHaveLength(8 + 3)
    const everything = asPayments(await app.api.get('/transactions'))
    expect(new Set(everything).size, 'a payment is in the ledger twice').toBe(everything.length)

    // The statement list says what each file covers, and what it brought.
    await expect(accountRows(page)).toHaveCount(3)
    await expect(accountRows(page).filter({ hasText: '5 Oct to 15 Nov' })).toHaveCount(1)
    const statements = await app.api.get('/statements')
    const overlap = statements.find((s) => s.period_end === '2026-11-15')
    expect([overlap.rows_in_file, overlap.rows_added, overlap.txn_count]).toEqual([15, 3, 3])
    // The balance of the whole file, though only three of its payments are here.
    expect(overlap.balance_check).toEqual({ checked: 14, consistent: 14, balanced: true })
  })

  test('the months after a file that holds them add what it lacks, and a file with nothing new is refused', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 409)
    await drop(page, FIXTURE_PDF_OVERLAP)
    await expect(summaryOf(page)).toContainText('Finished:')
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_NOV])
    await expect(fileRow(page, 'sample_statement.pdf')).toContainText('1 Oct to 8 Oct - 4 new payments, 4 already in your ledger')
    const refused = fileRow(page, 'sample_statement_nov.pdf')
    await expect(refused).toContainText('Nothing new: all 8 payments in this file are already in Bank ··0000.')
    await expect(refused).not.toContainText("Couldn't read this file")

    // No statement was made for the refused file, and nothing is doubled.
    expect(await app.api.get('/statements')).toHaveLength(2)
    expect(await app.api.get('/accounts')).toHaveLength(1)
    const everything = asPayments(await app.api.get('/transactions'))
    expect(everything).toHaveLength(15 + 4)
    expect(new Set(everything).size).toBe(everything.length)
  })

  test('every month a statement runs into is on offer, and the statement can be picked in it', async ({ page }) => {
    await drop(page, FIXTURE_PDF_OVERLAP)
    await expect(summaryOf(page)).toContainText('Finished:')
    await page.getByRole('link', { name: 'Transactions' }).first().click()
    await expect(page.getByRole('button', { name: 'Sort again' })).toBeVisible()
    const period = page.getByRole('combobox', { name: 'Period' })
    await expect(period.locator('option')).toHaveText(['All time', 'Nov 2026', 'Oct 2026'])
    await period.selectOption('2026-11')
    await expect(page.getByRole('combobox').first().locator('option')).toHaveText(['All statements', 'Bank ··0000 - 5 Oct to 15 Nov'])
    // The statement stays picked in November, which it runs into: the list is its November payments.
    await expect(page.getByRole('combobox').first()).toHaveValue(/\S/)
    await expect(page.getByText(/^11 payments/)).toBeVisible()
  })

  test('moving a statement into an account that holds some of its payments drops those, and says so', async ({ page, app }) => {
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_NOV])
    await expect(summaryOf(page)).toContainText('Finished:')
    // November goes to an account of its own; the overlap file then follows it there.
    const [first] = await app.api.get('/accounts')
    const nov = (await app.api.get('/statements')).find((s) => s.period_start === '2026-11-01')
    await app.api.patch(`/statements/${nov.id}`, { new_account: true, name: 'Second' })
    await drop(page, FIXTURE_PDF_OVERLAP)
    await expect(fileRow(page, 'sample_statement_overlap.pdf')).toContainText('7 new payments, 8 already in your ledger')

    await accountRows(page).filter({ hasText: '5 Oct to 15 Nov' }).getByRole('button', { name: /^More for / }).click()
    await page.getByRole('menuitem', { name: 'Move to account…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Move to account' })
    await dialog.getByRole('button', { name: 'Bank ··0000' }).first().click()
    await expect(dialog).toHaveCount(0)

    const moved = (await app.api.get('/statements')).find((s) => s.period_end === '2026-11-15')
    expect(moved.txn_count).toBe(3)
    expect(moved.rows_added).toBe(3)
    expect(moved.account_id).toBe(first.id)
    const everything = asPayments(await app.api.get('/transactions'))
    expect(new Set(everything).size).toBe(everything.length)
  })
})
