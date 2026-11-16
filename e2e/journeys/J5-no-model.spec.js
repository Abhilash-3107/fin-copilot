// J5 - No model connected. With both providers off, rows the rules cannot
// sort must still reach the user, and no screen may say "all done" while rows
// are unsorted or before anything has been uploaded.
import { test, expect } from '../fixtures.js'
import { open, openSettingsTab } from '../lib/ui.js'
import { sortedByServer, uploadFixture } from '../lib/statement.js'

test.use({ modelMode: 'none' })

async function openTeachMe(page) {
  await open(page, '/review')
  await expect(page.getByText('Getting your review list ready')).toBeHidden()
}

test.describe('J5 no model connected', () => {
  test("a fresh install's Teach Me does not say all done before anything is uploaded", async ({ page }) => {
    await openTeachMe(page)
    await expect(page.getByText(/all done|all caught up/i)).toHaveCount(0)
  })

  test('Settings says plainly that sorting uses your rules and remembered payees only', async ({ page }) => {
    await openSettingsTab(page, 'AI & Model')
    await expect(page.getByRole('status').filter({ hasText: /^Sorting is using/ })).toContainText('rules and remembered payees only')
  })

  test.describe('after a statement is sorted without a model', () => {
    let unsorted
    test.beforeEach(async ({ app }) => {
      const statement = await uploadFixture(app.api)
      const result = await sortedByServer(app.api, statement.id)
      expect(result.ai_active, 'the model must be off for this journey').toBe(false)
      unsorted = await app.api.get('/transactions?unannotated=true')
      expect(unsorted.length, 'rows the rules could not sort').toBeGreaterThan(0)
    })

    test('rows that got no category appear in Teach Me', async ({ page }) => {
      await openTeachMe(page)
      const progressText = page.getByText(/^\d+\s*\/\s*\d+$/).first()
      await expect(progressText, 'Teach Me should show a queue').toBeVisible({ timeout: 3000 })
      const progress = await progressText.innerText()
      const total = Number(progress.split('/')[1])
      expect(total).toBeGreaterThanOrEqual(unsorted.length)
    })

    test('Teach Me does not say all done while rows are unsorted', async ({ page }) => {
      await openTeachMe(page)
      await expect(page.getByText(/all done|all caught up/i)).toHaveCount(0)
    })
  })
})
