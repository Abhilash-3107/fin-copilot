// J2 - Numbers agree. The same fact must read the same on every screen, every
// total must equal its parts, and a drill-through must total what was clicked.
// Expected values come from the screen or the API, never from fixed rupees.
import { test, expect, expectedFailure } from '../fixtures.js'
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
  const card = sectionOf(page, /Recurring & Subscriptions/)
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
    const item = page.getByRole('link', { name: /no category/i })
    await expect(item).toBeVisible()
    const dashboard = firstRupees(await item.innerText())

    await open(page, '/insights')
    const row = (await breakdownRows(page)).find((r) => /^uncategori[sz]ed$/i.test(r.label))
    expect(row, 'an Uncategorized row on Money Map').toBeDefined()
    await row.item.getByRole('button').click()
    await row.item.getByRole('link', { name: /View transactions/ }).click()

    await expect(page).toHaveURL(/#\/transactions/)
    await waitForRows(page)
    const listed = await visibleSpend(page)
    // The Transactions summary line states money out when the page has one.
    const summary = page.getByText(/transactions? · ₹[\d,]+ out/)
    const summaryOut = (await summary.count()) > 0 ? rupeeFigures(await summary.first().innerText())[0] : listed

    expect({ dashboard, moneyMap: row.amount, summaryOut }).toEqual({ dashboard: listed, moneyMap: listed, summaryOut: listed })
  })

  test('a category drill-through from Money Map lists rows that sum to the figure clicked', async ({ page }) => {
    await open(page, '/insights')
    const row = page.getByRole('button', { name: /Food & Dining/ })
    const claimed = firstRupees(await row.locator('..').innerText())
    await row.click()
    await page.getByRole('link', { name: /View transactions/ }).first().click()

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
    expectedFailure('R1 item 9: Net is all transfers ever, and the page never says so')
    await open(page, '/people')
    await expect(page.getByRole('row').nth(1)).toBeVisible()
    await expect(page.getByRole('main')).toContainText(PERIOD_WORDS, { useInnerText: true })
  })

  test('a group says what its figure is, and does not call gross outflow "net"', async ({ page, app }) => {
    expectedFailure('R1 item 9: the group header labels the money it paid out as "net"')
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
