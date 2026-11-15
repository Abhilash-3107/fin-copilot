// J5 - No model connected. With both providers off, rows the rules cannot
// sort must still reach the user, and no screen may say "all done" while rows
// are unsorted or before anything has been uploaded.
import { test, expect, expectedFailure } from '../fixtures.js'
import { open } from '../lib/ui.js'
import { categorise, uploadFixture } from '../lib/statement.js'

test.use({ modelMode: 'none' })

async function openTeachMe(page) {
  await open(page, '/review')
  await expect(page.getByText('Getting your review list ready')).toBeHidden()
}

test.describe('J5 no model connected', () => {
  test("a fresh install's Teach Me does not say all done before anything is uploaded", async ({ page }) => {
    expectedFailure('R1 item 8: an empty Teach Me says "You\'re all done!" on a ledger that has nothing in it')
    await openTeachMe(page)
    await expect(page.getByText(/all done|all caught up/i)).toHaveCount(0)
  })

  test.describe('after a statement is categorised without a model', () => {
    let unsorted
    test.beforeEach(async ({ app }) => {
      const statement = await uploadFixture(app.api)
      const result = await categorise(app.api, statement.id)
      expect(result.ai_active, 'the model must be off for this journey').toBe(false)
      unsorted = await app.api.get('/transactions?unannotated=true')
      expect(unsorted.length, 'rows the rules could not sort').toBeGreaterThan(0)
    })

    test('rows that got no category appear in Teach Me', async ({ page }) => {
      expectedFailure('R1 item 8: rows with no annotation never enter the review queue')
      await openTeachMe(page)
      const progressText = page.getByText(/^\d+\s*\/\s*\d+$/).first()
      await expect(progressText, 'Teach Me should show a queue').toBeVisible({ timeout: 3000 })
      const progress = await progressText.innerText()
      const total = Number(progress.split('/')[1])
      expect(total).toBeGreaterThanOrEqual(unsorted.length)
    })

    test('Teach Me does not say all done while rows are unsorted', async ({ page }) => {
      expectedFailure('R1 item 8: Teach Me says "You\'re all done!" while the Dashboard counts rows with no category')
      await openTeachMe(page)
      await expect(page.getByText(/all done|all caught up/i)).toHaveCount(0)
    })
  })
})
