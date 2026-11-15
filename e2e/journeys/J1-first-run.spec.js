// J1 - First run, load demo data.
import { test, expect, expectedFailure } from '../fixtures.js'
import { figureNear, open } from '../lib/ui.js'

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
    expectedFailure('R1 item 10: the demo seeder writes no category ids, so Settings reports 35 false tidy-up warnings')
    await app.loadDemo()
    await open(page, '/settings')
    await page.getByRole('button', { name: 'Categories' }).click()
    await expect(page.getByText('Refine the subcategories')).toBeVisible()
    await expect(page.getByText('Tidy up')).toHaveCount(0)
  })

  test('demo data is visibly marked once loaded', async ({ page, app }) => {
    expectedFailure('R1 item 10: nothing on screen tells demo data from real data')
    await app.loadDemo()
    await open(page, '/dashboard')
    await expect(page.getByText(/demo data|sample data/i).first()).toBeVisible()
  })

  test('demo data can be removed without touching anything else', async ({ page, app }) => {
    expectedFailure('R1 item 10: only "Delete all data" exists, there is no way to remove just the demo data')
    await app.loadDemo()
    await open(page, '/settings')
    await page.getByRole('button', { name: 'Data' }).click()
    await page.getByRole('button', { name: /(remove|clear|delete) (the )?(demo|sample) data/i }).click()
    // A confirmation step is allowed.
    const confirm = page.getByRole('button', { name: /^(remove|clear|delete|confirm)/i }).last()
    await confirm.click({ timeout: 2000 }).catch(() => {})

    await open(page, '/dashboard')
    await expect(page.getByRole('button', { name: 'Load demo data' })).toBeVisible()
  })
})
