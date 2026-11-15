// J1 - First run, load demo data.
import { test, expect, expectedFailure } from '../fixtures.js'
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
    expectedFailure('R1 item 10: the demo seeder writes no category ids, so Settings reports 35 false tidy-up warnings')
    await app.loadDemo()
    await openSettingsTab(page, 'Categories')
    await expect(page.getByText('Refine the subcategories')).toBeVisible()
    await expect(page.getByText('Tidy up')).toHaveCount(0)
  })

  test('demo data is visibly marked once loaded', async ({ page, app }) => {
    expectedFailure('R1 item 10: nothing on screen tells demo data from real data')
    await app.loadDemo()
    await open(page, '/dashboard')
    await expect(page.getByText(/demo data|sample data/i).first()).toBeVisible()
  })

  test('removing the demo data leaves what the user added', async ({ page, app }) => {
    expectedFailure('R1 item 10: only "Delete all data" exists, there is no way to remove just the demo data')
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
})
