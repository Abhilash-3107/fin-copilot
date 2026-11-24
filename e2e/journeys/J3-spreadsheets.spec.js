// J3 (spreadsheets) - Add statements as CSV, XLSX or XLS. A layout nothing recognises is asked about
// once and remembered; a file that names its account lands by itself; a copy of a month the
// ledger already has adds only what is new, or waits for a yes when it matches nothing.
import { readFileSync } from 'node:fs'
import { test, expect } from '../fixtures.js'
import { open } from '../lib/ui.js'
import {
  FIXTURE_CSV, FIXTURE_CSV_EXPORT_NOV, FIXTURE_CSV_EXPORT_OCT, FIXTURE_CSV_OVERLAP, FIXTURE_PDF, FIXTURE_PDF_NOV,
  FIXTURE_XLS, FIXTURE_XLSX,
} from '../lib/statement.js'

const ADD = /^Add (\d+ statements|statement)$/
const fileRow = (page, name) => page.getByRole('listitem').filter({ hasText: name })
const asFile = (name, path, mimeType = 'text/csv') => ({ name, mimeType, buffer: readFileSync(path) })
const mappingForm = (page) => page.getByRole('group', { name: 'Check what each column is' })
const roleOf = (form, column) => form.getByRole('combobox', { name: `What column ${column} holds` })

async function drop(page, files) {
  await open(page, '/upload')
  await page.locator('input[type=file]').setInputFiles(files)
  await page.getByRole('button', { name: ADD }).click()
}

test.describe('J3 spreadsheets', () => {
  test('the page says spreadsheets work and takes all three kinds', async ({ page }) => {
    await open(page, '/upload')
    await expect(page.getByText('PDF, CSV, XLSX or XLS. You can add several at once.')).toBeVisible()
    await expect(page.getByText(/Spreadsheets \(CSV, XLSX, XLS\) from any bank work too/)).toBeVisible()
    await expect(page.locator('input[type=file]')).toHaveAttribute('accept', /\.csv.*\.xlsx.*\.xls/)
  })

  test('a long Add Statement page scrolls inside the app, never the window', async ({ page, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    // The books grid (a statement is in the ledger) and the mapping form (a layout nothing names) are
    // both on the page, which is longer than the window. Hidden text that is positioned must stay
    // inside the scrolling area, or the document itself grows past the window and the app shell
    // scrolls out of view.
    await drop(page, [asFile('october.pdf', FIXTURE_PDF, 'application/pdf')])
    await expect(fileRow(page, 'october.pdf')).toContainText('Balance checks out')
    await page.locator('input[type=file]').setInputFiles(asFile('export-oct.csv', FIXTURE_CSV_EXPORT_OCT))
    await page.getByRole('button', { name: ADD }).click()
    await expect(mappingForm(page)).toBeVisible()
    await expect(page.getByRole('region', { name: 'Your books' })).toBeVisible()
    const sizes = await page.evaluate(() => ({
      document: document.documentElement.scrollHeight, window: window.innerHeight, scrolled: window.scrollY,
      inside: document.querySelector('main').scrollHeight, shown: document.querySelector('main').clientHeight,
    }))
    expect(sizes.inside, 'the page is longer than its area, so the check means something').toBeGreaterThan(sizes.shown)
    expect(sizes.document, 'the document grew past the window').toBeLessThanOrEqual(sizes.window)
    expect(sizes.scrolled).toBe(0)
  })

  test('a layout nothing names is asked about once, then the next file lands by itself', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    await drop(page, asFile('export-oct.csv', FIXTURE_CSV_EXPORT_OCT))

    const form = mappingForm(page)
    await expect(form).toBeVisible()
    await expect(roleOf(form, 'When')).toHaveValue('date')
    await expect(roleOf(form, 'What')).toHaveValue('description')
    await expect(roleOf(form, 'Moved')).toHaveValue('amount')
    await expect(roleOf(form, 'Left')).toHaveValue('balance')
    // The first rows as they would be read: a negative amount is money out.
    const read = form.getByRole('table', { name: /How the first rows read/ })
    await expect(read).toContainText('2 Oct 2026')
    await expect(read).toContainText('−₹540.00')
    await expect(read).toContainText('+₹42,500.00')
    expect((await app.api.get('/statements')).length).toBe(0)

    // A choice that cannot be read says why beside the columns, and cannot be added.
    await roleOf(form, 'When').selectOption('')
    await expect(form.getByRole('alert')).toContainText('Choose which column is the Date')
    await form.getByLabel('Account', { exact: true }).selectOption('__new__')
    await form.getByLabel('Name for the new account').fill('Savings 2')
    await expect(form.getByRole('button', { name: 'Add this statement' })).toBeDisabled()
    await roleOf(form, 'When').selectOption('date')
    await expect(form.getByRole('alert')).toHaveCount(0)

    await form.getByLabel('Name for the new account').press('Enter')
    const row = fileRow(page, 'export-oct.csv')
    await expect(row).toContainText('6 payments added')
    await expect(row).toContainText('Balance checks out')
    await expect(row).toContainText('Added to a new account, Savings 2')
    await expect(mappingForm(page)).toHaveCount(0)

    // The next file with the same header asks nothing and goes to the same account.
    await page.locator('input[type=file]').setInputFiles(asFile('export-nov.csv', FIXTURE_CSV_EXPORT_NOV))
    await page.getByRole('button', { name: ADD }).click()
    const next = fileRow(page, 'export-nov.csv')
    await expect(next).toContainText('4 payments added')
    await expect(next).toContainText('Added to Savings 2')
    await expect(mappingForm(page)).toHaveCount(0)

    const accounts = await app.api.get('/accounts')
    expect(accounts.map((a) => a.account_label)).toEqual(['Savings 2'])
    expect(accounts[0].statement_count).toBe(2)
  })

  test('Escape leaves the file out and nothing is stored', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    await drop(page, asFile('export-oct.csv', FIXTURE_CSV_EXPORT_OCT))
    const form = mappingForm(page)
    await expect(roleOf(form, 'When')).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(mappingForm(page)).toHaveCount(0)
    await expect(fileRow(page, 'export-oct.csv')).toHaveCount(0)
    expect(await app.api.get('/statements')).toEqual([])
  })

  test('a file that names its account lands beside the PDFs, and an overlapping CSV adds only what is new', async ({ page, app }) => {
    await drop(page, [asFile('oct.pdf', FIXTURE_PDF, 'application/pdf'), asFile('nov.pdf', FIXTURE_PDF_NOV, 'application/pdf')])
    await expect(fileRow(page, 'oct.pdf')).toContainText('8 payments added')
    await expect(fileRow(page, 'nov.pdf')).toContainText('8 payments added')

    await page.locator('input[type=file]').setInputFiles(asFile('overlap.csv', FIXTURE_CSV_OVERLAP))
    await page.getByRole('button', { name: ADD }).click()
    const row = fileRow(page, 'overlap.csv')
    // The narrations are cut short, so only the running balances say which payments the PDFs had.
    await expect(row).toContainText('3 new payments, 12 already in your ledger')
    await expect(row).toContainText('Added to Bank ··0000')
    await expect(mappingForm(page)).toHaveCount(0)

    const accounts = await app.api.get('/accounts')
    expect(accounts).toHaveLength(1)
    const statements = await app.api.get('/statements')
    expect(statements.reduce((total, s) => total + s.txn_count, 0)).toBe(19)
  })

  // October as a bare export: no balance column, narrations cut short. Every day is the 8th or
  // earlier, so nothing in the file says which way round the dates go. `named` keeps the account
  // number above the table; without it the file names no account at all.
  const bareExport = (named) => {
    const lines = readFileSync(FIXTURE_CSV, 'utf8').trimEnd().split('\r\n')
    return lines.map((line) => {
      const cells = line.match(/("[^"]*"|[^,]*)(,|$)/g).map((c) => c.replace(/,$/, ''))
      if (/^\d\d\//.test(cells[0])) return [cells[0], cells[1].slice(0, 10), cells[2], cells[3]].join(',')
      return cells[0] === 'Date' ? cells.slice(0, 4).join(',') : line
    }).filter((line) => named || !/^(Example Bank|Account Number|Statement Period)/.test(line)).join('\r\n')
  }
  const dropBare = async (page, named) => {
    await page.locator('input[type=file]').setInputFiles({ name: 'copy.csv', mimeType: 'text/csv', buffer: Buffer.from(bareExport(named)) })
    await page.getByRole('button', { name: ADD }).click()
  }

  test('a copy of a month printed another way asks which way round, then waits for a yes before anything is added', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    await drop(page, asFile('oct.pdf', FIXTURE_PDF, 'application/pdf'))
    await expect(fileRow(page, 'oct.pdf')).toContainText('8 payments added')

    await dropBare(page, true)
    // The file names its account, so only the dates are asked, with the account already chosen.
    const dates = page.getByRole('group', { name: 'Which way round are the dates?' })
    await expect(dates.getByRole('radio', { name: /Day first/ })).toBeChecked()
    await expect(dates.getByLabel('Account', { exact: true })).toHaveValue(/^acc_/)
    await dates.getByRole('button', { name: 'Add this statement' }).click()

    const alert = fileRow(page, 'copy.csv').getByRole('alert')
    await expect(alert).toContainText('all 8 of its payments on those days match nothing there')
    await expect(alert).toContainText('count those payments twice')
    // The safe action has focus.
    await expect(fileRow(page, 'copy.csv').getByRole('button', { name: 'Leave it out' })).toBeFocused()
    expect((await app.api.get('/statements')).length).toBe(1)

    await fileRow(page, 'copy.csv').getByRole('button', { name: 'Leave it out' }).click()
    await expect(fileRow(page, 'copy.csv')).toHaveCount(0)
    // Focus goes to the row beside it (oct.pdf, the one other file on the page), never the page.
    await expect(fileRow(page, 'oct.pdf')).toBeFocused()
    expect((await app.api.get('/statements')).length).toBe(1)

    // The person can still say yes: the columns and the way round are sent again with it.
    await dropBare(page, true)
    await page.getByRole('group', { name: 'Which way round are the dates?' }).getByRole('button', { name: 'Add this statement' }).click()
    await fileRow(page, 'copy.csv').getByRole('button', { name: 'Add it anyway' }).click()
    await expect(fileRow(page, 'copy.csv')).toContainText('8 payments added')
    await expect(fileRow(page, 'copy.csv')).toContainText('No running balance in this file, so it could not be checked')
  })

  test('a copy that names no account lands after the form and Add it anyway', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    await drop(page, asFile('oct.pdf', FIXTURE_PDF, 'application/pdf'))
    await expect(fileRow(page, 'oct.pdf')).toContainText('8 payments added')

    await dropBare(page, false)
    const ask = page.getByRole('group', { name: 'Which account is this file from?' })
    await expect(ask.getByRole('radio', { name: /Day first/ })).toBeChecked()
    await ask.getByLabel('Account', { exact: true }).selectOption({ index: 1 })
    await ask.getByRole('button', { name: 'Add this statement' }).click()
    await expect(fileRow(page, 'copy.csv').getByRole('alert')).toContainText('match nothing there')

    // The held notice sends everything the form gave, so nothing is asked a second time.
    await fileRow(page, 'copy.csv').getByRole('button', { name: 'Add it anyway' }).click()
    await expect(fileRow(page, 'copy.csv')).toContainText('8 payments added')
    expect((await app.api.get('/statements')).length).toBe(2)
    expect(await app.api.get('/accounts')).toHaveLength(1)
  })

  test('an XLSX and an XLS of a month read like its PDF', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    await tolerate(/\/api\/statements\/upload/, 409)
    await drop(page, asFile('month.xlsx', FIXTURE_XLSX, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
    // The workbook has no account yet to name its number: only the account is asked.
    const ask = page.getByRole('group', { name: 'Which account is this file from?' })
    await expect(ask).toBeVisible()
    await ask.getByLabel('Account', { exact: true }).selectOption('__new__')
    await ask.getByLabel('Name for the new account').fill('Main')
    await ask.getByRole('button', { name: 'Add this statement' }).click()
    await expect(fileRow(page, 'month.xlsx')).toContainText('8 payments added')
    await expect(fileRow(page, 'month.xlsx')).toContainText('Balance checks out')

    // The same month again as an old .xls: the balances say every payment is already in.
    await page.locator('input[type=file]').setInputFiles(asFile('older.xls', FIXTURE_XLS, 'application/vnd.ms-excel'))
    await page.getByRole('button', { name: ADD }).click()
    await expect(fileRow(page, 'older.xls')).toContainText('Nothing new: all 8 payments in this file are already in Main.')
    expect((await app.api.get('/statements')).length).toBe(1)
  })
})
