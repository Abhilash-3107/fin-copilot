// J2 - Numbers agree. The same fact must read the same on every screen, every
// total must equal its parts, and a drill-through must total what was clicked.
// Expected values come from the screen or the API, never from fixed rupees.
import { test, expect, expectedFailure } from '../fixtures.js'
import {
  PERIOD_WORDS, figureNear, firstRupees, open, rupeeFigures, sectionOf, visibleSpend, waitForRows,
} from '../lib/ui.js'

test.describe('J2 numbers agree', () => {
  test.beforeEach(async ({ app }) => {
    await app.loadDemo()
  })

  test('Kept is one figure on the Dashboard headline, the Dashboard tile and Money Map', async ({ page }) => {
    expectedFailure('R1 item 2: the headline is In - Out, the tile and Money Map also subtract Invested')
    await open(page, '/dashboard')
    const headline = firstRupees(await page.getByText(/you (kept|went over by)/).first().innerText())
    const tile = await figureNear(page, 'Kept')
    await open(page, '/insights')
    const moneyMap = await figureNear(page, 'Kept')

    expect({ headline, tile, moneyMap }).toEqual({ headline: tile, tile, moneyMap: tile })
  })

  test('the Money Map category breakdown sums to Out', async ({ page }) => {
    expectedFailure('R1 item 4: cash withdrawals and transfers to people are in Out but have no row in the breakdown')
    await open(page, '/insights')
    const out = await figureNear(page, 'Out')

    const breakdown = sectionOf(page, /^Where it went/)
    await expect(breakdown).toBeVisible()
    // A category's amount stands alone on its line. Deltas carry an arrow and
    // the footnotes carry words, so they do not count.
    const lines = (await breakdown.innerText()).split('\n').map((l) => l.trim())
    const amounts = lines.filter((l) => /^[−-]?₹[\d,]+(\.\d+)?$/.test(l)).map(firstRupees)
    expect(amounts.length, 'category rows found').toBeGreaterThan(3)

    expect(amounts.reduce((a, b) => a + b, 0)).toBe(out)
  })

  test('the recurring header total equals the sum of its rows', async ({ page }) => {
    expectedFailure('R1 item 6: the header counts monthly items only, the rows include one that is not monthly')
    await open(page, '/insights')
    const panel = sectionOf(page, /Recurring & Subscriptions/)
    const header = firstRupees(await panel.getByText(/\/mo across/).locator('..').innerText())

    const rows = panel.getByRole('row').filter({ hasNotText: 'Stopped' })
    await expect(rows.first()).toBeVisible()
    const amounts = await rows.getByRole('cell').filter({ hasText: /^\s*₹[\d,]+$/ }).allInnerTexts()
    expect(amounts.length, 'recurring rows found').toBeGreaterThan(3)

    expect(amounts.map(firstRupees).reduce((a, b) => a + b, 0)).toBe(header)
  })

  test('the recurring header total equals what the Dashboard says is committed each month', async ({ page }) => {
    expectedFailure('R1 item 6: Money Map and the Dashboard disagree on the monthly commitment')
    await open(page, '/insights')
    const moneyMap = firstRupees(
      await sectionOf(page, /Recurring & Subscriptions/).getByText(/\/mo across/).locator('..').innerText(),
    )
    await open(page, '/dashboard')
    const dashboard = await figureNear(page, 'Spoken for each month')

    expect(moneyMap).toBe(dashboard)
  })

  test('the Dashboard uncategorised figure equals the total of the transactions it links to', async ({ page }) => {
    expectedFailure('R1 item 5: the link opens Teach Me, and the Dashboard shows two different uncategorised totals')
    await open(page, '/dashboard')
    const item = page.getByRole('link', { name: /no category/i })
    await expect(item).toBeVisible()
    const claimed = firstRupees(await item.innerText())

    await item.click()
    await expect(page, 'the link should open a list of those transactions').toHaveURL(/#\/transactions/)
    await waitForRows(page)
    expect(await visibleSpend(page)).toBe(claimed)
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
    expectedFailure('R1 item 9: the people panel shows sent, received and a net with no period')
    await open(page, '/insights')
    const panel = sectionOf(page, /^Between you and/)
    await expect(panel).toBeVisible()
    expect(rupeeFigures(await panel.innerText()).length, 'person figures found').toBeGreaterThan(0)
    await expect(panel).toContainText(PERIOD_WORDS)
  })

  test('People says what each per-person figure counts and over what period', async ({ page }) => {
    expectedFailure('R1 item 9: Net is all transfers ever, and the page never says so')
    await open(page, '/people')
    await expect(page.getByRole('row').nth(1)).toBeVisible()
    await expect(page.getByRole('main')).toContainText(PERIOD_WORDS)
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
