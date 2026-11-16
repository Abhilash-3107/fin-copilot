// J1 - First run, load demo data.
import { test, expect } from '../fixtures.js'
import { figureNear, open, openSettingsTab } from '../lib/ui.js'

test.describe('J1 first run', () => {
  test('a fresh app offers to load demo data', async ({ page }) => {
    await open(page, '/dashboard')
    await expect(page.getByRole('button', { name: 'Load demo data' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Add your first statement' })).toBeVisible()
  })

  test('loading demo data fills the home screen', async ({ page }) => {
    await open(page, '/dashboard')
    await page.getByRole('button', { name: 'Load demo data' }).click()

    for (const label of ['In', 'Out', 'Kept']) {
      expect(await figureNear(page, label), `${label} figure`).toBeGreaterThan(0)
    }
    await expect(page.getByRole('button', { name: 'Load demo data' })).toHaveCount(0)
  })

  test('Settings > Categories shows no tidy-up warnings', async ({ page, app }) => {
    await app.loadDemo()
    await openSettingsTab(page, 'Categories')
    await expect(page.getByText('Refine the subcategories')).toBeVisible()
    await expect(page.getByText('Tidy up')).toHaveCount(0)
  })

  test('demo data is visibly marked once loaded', async ({ page, app }) => {
    await app.loadDemo()
    await open(page, '/dashboard')
    await expect(page.getByText(/demo data|sample data/i).first()).toBeVisible()
  })

  test('removing the demo data leaves what the user added', async ({ page, app }) => {
    await app.loadDemo()
    // Things the user made themselves, which removal must not touch.
    const person = await app.api.post('/people', { name: 'E2E Friend', upi: 'e2e.friend@upi', relationship: 'friend' })
    const rule = await app.api.post('/rules', { pattern: 'bookstore', category: 'Shopping', subcategory: 'General Retail' })
    await app.api.put('/config', { confidence_threshold: 0.7 })

    await openSettingsTab(page, 'Data')
    await page.getByRole('main').getByRole('button', { name: 'Remove sample data', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Remove sample data', exact: true }).click()
    // The app returns to the Dashboard by itself once the data is gone.
    await expect(page.getByRole('button', { name: 'Load demo data' })).toBeVisible()

    expect(await app.api.get('/statements')).toEqual([])
    expect((await app.api.get('/people')).map((p) => p.id)).toEqual([person.id])
    expect((await app.api.get('/rules')).map((r) => r.id)).toEqual([rule.id])
    expect((await app.api.get('/config')).confidence_threshold).toBe(0.7)
  })

  // The search opens what it found, wherever that is: another month than the one on
  // screen, with the payment's panel open, and a person's results are their payments,
  // not a text search that can land on "No transactions".
  test('search opens the exact payment from another month', async ({ page, app }) => {
    await app.loadDemo()
    const [payment] = await app.api.get('/transactions?q=September%20rent')
    expect(payment, 'the demo payment noted "September rent"').toBeDefined()
    expect(payment.txn_date.slice(0, 7)).toBe('2026-09')

    await open(page, '/dashboard')
    await page.keyboard.press('Control+K')
    await page.getByRole('combobox', { name: 'Search' }).fill('September rent')
    await page.getByRole('option', { name: /01 Sep/ }).click()

    await expect(page.getByText('As printed on the statement')).toBeVisible()
    await expect(page.getByText(payment.raw_description)).toBeVisible()
    // The period followed the payment, and its row is on the page behind the panel.
    await expect(page.locator('select').filter({ has: page.locator('option[value="2026-09"]') })).toHaveValue('2026-09')
    await expect(page.getByRole('row', { name: /01 Sep 26 Amit Kumar September rent/ })).toBeVisible()
    await expect(page).not.toHaveURL(/txn=/)
  })

  test('search for a person lists their payments across every month', async ({ page, app }) => {
    await app.loadDemo()
    await open(page, '/dashboard')
    await page.getByRole('button', { name: 'Search' }).click()
    await page.getByRole('combobox', { name: 'Search' }).fill('neha')
    await page.getByRole('option', { name: /Neha Gupta/ }).filter({ hasText: 'person' }).click()

    await expect(page.getByRole('button', { name: /Person: Neha Gupta/ })).toBeVisible()
    await expect(page.getByRole('row').nth(1)).toBeVisible()
    await expect(page.getByText('No transactions')).toHaveCount(0)
    await expect(page.locator('select').filter({ has: page.locator('option[value="all"]') })).toHaveValue('all')
  })

  test('a repeated payee is one result that filters to all of its payments', async ({ page, app }) => {
    await app.loadDemo()
    await open(page, '/dashboard')
    await page.keyboard.press('Control+K')
    await page.getByRole('combobox', { name: 'Search' }).fill('swiggy')
    const payee = page.getByRole('option', { name: /Swiggy - all payments/i })
    await expect(payee).toHaveCount(1)
    await payee.click()
    await expect(page.getByRole('button', { name: /Payee: Swiggy/i })).toBeVisible()
    await expect(page.getByRole('row').nth(1)).toBeVisible()
  })
})
