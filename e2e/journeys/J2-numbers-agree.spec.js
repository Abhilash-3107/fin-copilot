// J2 - Numbers agree. The same fact must read the same on every screen, every
// total must equal its parts, and a drill-through must total what was clicked.
// Expected values come from the screen or the API, never from fixed rupees.
import { test, expect } from '../fixtures.js'
import {
  PERIOD_WORDS, figureNear, firstRupees, open, rupeeFigures, sectionOf, visibleSpend, waitForRows,
} from '../lib/ui.js'

// A figure that stands alone on its line, as an amount does in a list row.
// Deltas carry an arrow and notes carry words, so they do not match.
const BARE_AMOUNT = /^[−-]?₹[\d,]+(\.\d+)?$/

const sum = (numbers) => Math.round(numbers.reduce((a, b) => a + b, 0) * 100) / 100

/** Read one list row as the user sees it: its label and the amount on its own line. */
async function readRow(item) {
  const lines = (await item.innerText()).split('\n').map((l) => l.trim()).filter(Boolean)
  const amount = lines.find((l) => BARE_AMOUNT.test(l))
  return { label: lines[0].replace(/^[▸▾]\s*/, ''), amount: amount === undefined ? null : firstRupees(amount) }
}

/**
 * The Ledger rows whose payee cell reads `name`. A row's accessible name holds every cell,
 * so a name like "Rent" would also match the "Housing > Rent" subcategory of other rows.
 */
const rowsPaidTo = (page, name) =>
  page.getByRole('row').filter({ has: page.getByRole('cell', { name: new RegExp(`^${name}(\\s|$)`) }) })

/** The Money Map breakdown card and its top-level rows, one per category or reconciling line. */
async function breakdownRows(page) {
  const card = sectionOf(page, /^Where it went/)
  await expect(card).toBeVisible()
  const items = card.locator(':scope > ul > li')
  await expect(items.first()).toBeVisible()
  const rows = []
  for (const item of await items.all()) rows.push({ item, ...(await readRow(item)) })
  return rows
}

/** The Money Map recurring card: its header total and its rows in the order shown. */
async function recurringPanel(page) {
  const card = sectionOf(page, /Recurring and subscriptions/)
  await expect(card).toBeVisible()
  const header = firstRupees(await card.getByText(/\/mo across/).locator('..').innerText())
  const rows = []
  for (const tr of await card.locator('tbody > tr').all()) {
    const cells = await tr.locator('td').allInnerTexts()
    // A heading row inside the table ("not in the monthly total", "Stopped") has one cell.
    rows.push(cells.length === 1 ? { heading: cells[0].trim() } : { amount: firstRupees(cells.at(-1)), text: cells.join(' ') })
  }
  // The header counts the monthly charges that are still running: the rows
  // above the first heading. Everything after a heading is listed for
  // information only. Where there are no headings, every row is counted.
  const firstHeading = rows.findIndex((r) => r.heading !== undefined)
  const counted = rows.slice(0, firstHeading === -1 ? rows.length : firstHeading)
  return { header, monthlyRows: counted, all: rows.filter((r) => r.heading === undefined) }
}

test.describe('J2 numbers agree', () => {
  test.beforeEach(async ({ app }) => {
    await app.loadDemo()
  })

  test('Kept is one figure on the Dashboard headline, the Dashboard tile and Money Map', async ({ page }) => {
    await open(page, '/dashboard')
    const sentence = await page.getByText(/you (kept|went over by)|spent\s+₹[\d,]+\s+more than came in/).first().innerText()
    // The headline words a shortfall as a positive amount; the tiles show it as a negative.
    const headline = /went over by|more than came in/.test(sentence) ? -firstRupees(sentence) : firstRupees(sentence)
    const tile = await figureNear(page, 'Kept')
    await open(page, '/insights')
    const moneyMap = await figureNear(page, 'Kept')

    expect({ headline, tile, moneyMap }).toEqual({ headline: tile, tile, moneyMap: tile })
  })

  test('the Money Map category breakdown sums to Out', async ({ page }) => {
    await open(page, '/insights')
    const out = await figureNear(page, 'Out')

    const rows = await breakdownRows(page)
    const total = rows.filter((r) => /^total( out)?$/i.test(r.label))
    expect(total, 'a closing "Total out" row').toHaveLength(1)
    const parts = rows.filter((r) => !total.includes(r))
    expect(parts.length, 'category rows found').toBeGreaterThan(3)
    expect(parts.filter((r) => r.amount === null), 'rows without an amount').toEqual([])

    expect(sum(parts.map((r) => r.amount))).toBe(out)
    expect(total[0].amount).toBe(out)
  })

  test('the recurring header total equals the sum of its rows', async ({ page }) => {
    await open(page, '/insights')
    const { header, monthlyRows } = await recurringPanel(page)
    expect(monthlyRows.length, 'monthly recurring rows found').toBeGreaterThan(3)

    expect(sum(monthlyRows.map((r) => r.amount))).toBe(header)
  })

  test('the recurring header total equals what the Dashboard says is committed each month', async ({ page }) => {
    await open(page, '/insights')
    const { header, monthlyRows } = await recurringPanel(page)
    await open(page, '/dashboard')
    const dashboard = await figureNear(page, 'Spoken for each month')

    // One commitment, three places: the Money Map header, its rows, the Dashboard.
    expect({ dashboard, header, rows: sum(monthlyRows.map((r) => r.amount)) }).toEqual({ dashboard: header, header, rows: header })
  })

  test('the Dashboard uncategorised figure equals the Money Map row and the transactions behind it', async ({ page }) => {
    await open(page, '/dashboard')
    // Where this link goes is not judged here, only the figure it states.
    const item = page.getByRole('link', { name: /(aren't|isn't) sorted yet/i })
    await expect(item).toBeVisible()
    const dashboard = firstRupees(await item.innerText())

    await open(page, '/insights')
    const row = (await breakdownRows(page)).find((r) => /^uncategori[sz]ed$/i.test(r.label))
    expect(row, 'an Uncategorized row on Money Map').toBeDefined()
    await row.item.getByRole('button').click()
    await row.item.getByRole('link', { name: /View payments/ }).click()

    await expect(page).toHaveURL(/#\/transactions/)
    await waitForRows(page)
    const listed = await visibleSpend(page)
    // The Transactions summary line states money out when the page has one.
    const summary = page.getByText(/payments? · ₹[\d,]+ out/)
    const summaryOut = (await summary.count()) > 0 ? rupeeFigures(await summary.first().innerText())[0] : listed

    expect({ dashboard, moneyMap: row.amount, summaryOut }).toEqual({ dashboard: listed, moneyMap: listed, summaryOut: listed })
  })

  test('a category drill-through from Money Map lists rows that sum to the figure clicked', async ({ page }) => {
    await open(page, '/insights')
    const row = page.getByRole('button', { name: /Food & Dining/ })
    const claimed = firstRupees(await row.locator('..').innerText())
    await row.click()
    await page.getByRole('link', { name: /View payments/ }).first().click()

    await expect(page).toHaveURL(/#\/transactions/)
    await waitForRows(page)
    expect(await visibleSpend(page)).toBe(claimed)
  })

  test('Money Map says what each per-person figure counts and over what period', async ({ page }) => {
    await open(page, '/insights')
    // Found by what it holds, so a retitled panel is still the panel.
    const person = page.getByRole('main').getByRole('listitem').filter({ hasText: /sent\s+₹[\d,]+\s*·\s*received\s+₹/ }).first()
    await expect(person, 'a person row with sent and received figures').toBeVisible()
    const panel = person.locator('xpath=ancestor::ul[1]/parent::div')

    expect(rupeeFigures(await panel.innerText()).length, 'person figures found').toBeGreaterThan(0)
    await expect(panel).toContainText(PERIOD_WORDS, { useInnerText: true })
  })

  test('People says what each per-person figure counts and over what period', async ({ page }) => {
    await open(page, '/people')
    await expect(page.getByRole('row').nth(1)).toBeVisible()
    await expect(page.getByRole('main')).toContainText(PERIOD_WORDS, { useInnerText: true })
  })

  test('a group says what its figure is, and does not call gross outflow "net"', async ({ page, app }) => {
    const [group] = await app.api.get('/groups')
    await open(page, '/groups')
    const header = page.getByRole('button', { name: `Expand ${group.name}` }).locator('..')
    await expect(header).toBeVisible()
    // The group's own note can say anything. Judge only what the app wrote.
    const text = (await header.innerText()).replace(group.note, '')

    expect(text).not.toMatch(/\bnet\b/i)
    expect(text).toMatch(/your share|settled/i)
  })
})

// A name is a label: renaming a payee or a payment changes what rows are
// called, on every screen, and moves no figure.
test.describe('J2 renaming', () => {
  test.beforeEach(async ({ app }) => {
    await app.loadDemo()
  })

  /** Open a payment's panel from the Ledger and start renaming it. */
  async function startRename(page, rowName) {
    await open(page, '/transactions')
    await waitForRows(page)
    await page.getByRole('row', { name: rowName }).first().click()
    await expect(page.getByText('As printed on the statement')).toBeVisible()
    await page.getByRole('button', { name: 'Rename' }).click()
  }

  test('a payee renamed to Rent reads Rent on the Ledger, Money Map and Dashboard, and no figure moves', async ({ page, app }) => {
    await open(page, '/insights')
    const before = await recurringPanel(page)
    expect(before.all.some((r) => r.text.includes('Amit Kumar')), 'the landlord among the recurring rows').toBe(true)

    await open(page, '/transactions')
    await waitForRows(page)
    // Two lines: the payee, then the note and the channel. The bank line is not on the row.
    const row = page.getByRole('row', { name: /Amit Kumar/ }).first()
    await expect(row).toContainText(/November rent\s*·\s*UPI/)
    await expect(row).not.toContainText('AMIT.LANDLORD@OKSBI')

    await row.click()
    await expect(page.getByText('As printed on the statement')).toBeVisible()
    await expect(page.getByText(/UPI\/AMIT\.LANDLORD@OKSBI\//)).toBeVisible()
    await page.getByRole('button', { name: 'Rename' }).click()
    await expect(page.getByRole('radio', { name: 'Every payment to Amit Kumar' })).toBeChecked()
    await page.getByRole('textbox', { name: 'Name' }).fill('Rent')
    await page.getByRole('button', { name: 'Save name' }).click()
    await expect(page.getByRole('heading', { name: 'Rent' })).toBeVisible()
    await page.keyboard.press('Escape')

    await expect(rowsPaidTo(page, 'Rent').first()).toBeVisible()
    await expect(rowsPaidTo(page, 'Amit Kumar')).toHaveCount(0)
    await expect(rowsPaidTo(page, 'Rent').first()).toContainText('November rent')

    // Every payment to the payee, past ones too, and nothing else changed.
    const payments = (await app.api.get('/transactions?include=annotation')).filter((t) => t.counterparty_key === 'AMIT.LANDLORD@OKSBI')
    expect(payments.length).toBeGreaterThan(1)
    for (const t of payments) {
      expect(t.payee).toBe('Rent')
      expect(t.raw_description).toMatch(/^UPI\/AMIT\.LANDLORD@OKSBI\//)
      expect(t.merchant).toBe('Amit Kumar')
    }

    await open(page, '/insights')
    const after = await recurringPanel(page)
    expect(after.all.some((r) => r.text.includes('Rent'))).toBe(true)
    expect(after.all.some((r) => r.text.includes('Amit Kumar'))).toBe(false)
    expect({ header: after.header, amounts: after.all.map((r) => r.amount) }).toEqual({
      header: before.header, amounts: before.all.map((r) => r.amount),
    })

    await open(page, '/dashboard')
    await expect(page.getByRole('main')).toContainText('Rent')
    await expect(page.getByRole('main')).not.toContainText('Amit Kumar')

    // The name is searchable: every row the search finds is paid to Rent (the header row is the one extra).
    await open(page, '/transactions?q=Rent')
    await waitForRows(page)
    const found = await page.getByRole('row').count()
    expect(found).toBeGreaterThan(1)
    await expect(rowsPaidTo(page, 'Rent')).toHaveCount(found - 1)
  })

  test('the Rent row on Money Map opens exactly the landlord\'s payments, even when another payment is also called Rent', async ({ page, app }) => {
    const all = await app.api.get('/transactions?include=annotation')
    const landlord = all.filter((t) => t.counterparty_key === 'AMIT.LANDLORD@OKSBI')
    const swiggy = all.find((t) => t.payee === 'Swiggy')
    await app.api.put(`/transactions/${landlord[0].id}/payee-name`, { name: 'Rent' })
    await app.api.put(`/transactions/${swiggy.id}/name`, { name: 'Rent' })

    // A recurring row spans months, so look at all of them rather than the one the period picks.
    await page.addInitScript(() => localStorage.setItem('fc_period_month', 'all'))
    await open(page, '/insights')
    const card = sectionOf(page, /Recurring and subscriptions/)
    await card.getByRole('row', { name: /Rent/ }).first().click()

    await expect(page).toHaveURL(/payee_key=AMIT\.LANDLORD%40OKSBI/)
    await waitForRows(page)
    await expect(page.getByRole('button', { name: /Payee: Rent/ })).toBeVisible()
    await expect(page.getByRole('row')).toHaveCount(landlord.length + 1)
    await expect(rowsPaidTo(page, 'Rent')).toHaveCount(landlord.length)
    // The one count the page states agrees with the rows the API says were paid to the landlord.
    await expect(page.getByText(new RegExp(`${landlord.length} payments?`)).first()).toBeVisible()
  })

  test('a single payment renamed changes that payment and no other', async ({ page, app }) => {
    const swiggy = (await app.api.get('/transactions?include=annotation')).filter((t) => t.payee === 'Swiggy')
    expect(swiggy.length).toBeGreaterThan(2)

    await startRename(page, /Swiggy/)
    await page.getByRole('radio', { name: 'Just this payment' }).check()
    await page.getByRole('textbox', { name: 'Name' }).fill('Office lunch')
    await page.getByRole('button', { name: 'Save name' }).click()
    await expect(page.getByRole('heading', { name: 'Office lunch' })).toBeVisible()
    await page.keyboard.press('Escape')

    await expect(page.getByRole('row', { name: /Office lunch/ })).toHaveCount(1)
    const now = await app.api.get('/transactions?include=annotation')
    const renamed = now.filter((t) => t.payee === 'Office lunch')
    expect(renamed).toHaveLength(1)
    expect(now.filter((t) => t.payee === 'Swiggy')).toHaveLength(swiggy.length - 1)
    // The key and the printed line are untouched, so insights still group it with Swiggy.
    const original = swiggy.find((t) => t.id === renamed[0].id)
    expect(renamed[0].counterparty_key).toBe(original.counterparty_key)
    expect(renamed[0].raw_description).toBe(original.raw_description)
  })

  // A note is a label too: the person's own words replace the UPI note on the
  // row, the search finds them, clearing brings the UPI note back, and no
  // figure and no printed line moves.
  test('a note on a payment shows on its row, is found by search, and clearing it brings the UPI note back', async ({ page, app }) => {
    const [swiggy] = (await app.api.get('/transactions?include=annotation')).filter((t) => t.payee === 'Swiggy')
    const upiNote = swiggy.note
    expect(upiNote, 'a Swiggy payment with a UPI note').toBeTruthy()
    const figures = () => app.api.get('/transactions/summary')
    const before = await figures()

    await startRename(page, /Swiggy/)
    // Opening the note form closes the rename form, and starts from the note the row shows.
    await page.getByRole('button', { name: 'Edit note' }).click()
    await expect(page.getByRole('textbox', { name: 'Name' })).toHaveCount(0)
    await expect(page.getByRole('textbox', { name: 'Your note' })).toHaveValue(upiNote)
    await page.getByRole('textbox', { name: 'Your note' }).fill('Zebra crossing party')
    await page.keyboard.press('Enter')
    const panel = page.locator('aside').filter({ hasText: 'As printed on the statement' })
    await expect(panel).toContainText('Your noteZebra crossing party')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('row', { name: /Swiggy Zebra crossing party\s*·\s*UPI/ })).toHaveCount(1)

    // The line as printed, the key and the figures are as they were.
    const noted = await app.api.get(`/transactions/${swiggy.id}`)
    expect(noted.note).toBe('Zebra crossing party')
    expect(noted.raw_description).toBe(swiggy.raw_description)
    expect(noted.counterparty_key).toBe(swiggy.counterparty_key)
    expect(await figures()).toEqual(before)

    // The palette finds the payment by the note, and the Ledger search lists it.
    await open(page, '/dashboard')
    await page.keyboard.press('Control+K')
    await page.getByRole('combobox', { name: 'Search' }).fill('zebra crossing')
    await page.getByRole('option', { name: /Swiggy/ }).click()
    await expect(page.getByText('As printed on the statement')).toBeVisible()
    await expect(panel).toContainText(swiggy.raw_description)
    await expect(panel).toContainText('Zebra crossing party')
    await page.keyboard.press('Escape')
    await open(page, '/transactions?q=zebra')
    await waitForRows(page)
    await expect(page.getByRole('row')).toHaveCount(2)

    // Clear it: the UPI note is back on the row, and the search no longer finds it.
    await page.getByRole('row', { name: /Zebra crossing party/ }).click()
    await page.getByRole('button', { name: 'Edit note' }).click()
    await page.getByRole('button', { name: 'Clear note' }).click()
    await expect(page.getByRole('button', { name: 'Edit note' })).toBeVisible()
    await expect(panel).toContainText(upiNote)
    expect((await app.api.get(`/transactions/${swiggy.id}`)).note).toBe(upiNote)
    expect(await app.api.get('/transactions?q=zebra')).toEqual([])
  })
})
