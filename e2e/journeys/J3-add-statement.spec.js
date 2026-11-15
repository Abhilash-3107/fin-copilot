// J3 - Add a statement. Upload the fixture, see the balance check pass, see
// the queue fill.
import { test, expect, expectedFailure } from '../fixtures.js'
import { open } from '../lib/ui.js'
import { FIXTURE_PDF } from '../lib/statement.js'

async function uploadThroughThePage(page) {
  await open(page, '/upload')
  await page.locator('input[type=file]').setInputFiles(FIXTURE_PDF)
  await page.getByRole('button', { name: 'Upload', exact: true }).click()
  const row = page.getByRole('row').filter({ hasText: 'generic' })
  await expect(row).toBeVisible()
  return row
}

test.describe('J3 add a statement', () => {
  test('an uploaded statement shows its balance check passing', async ({ page, app }) => {
    const row = await uploadThroughThePage(page)

    const [statement] = await app.api.get('/statements')
    expect(statement.balance_check.balanced).toBe(true)
    expect(statement.balance_check.checked).toBeGreaterThan(0)
    const { consistent, checked } = statement.balance_check
    await expect(row).toContainText(`${consistent}/${checked}`)
  })

  test('categorising the statement fills the Teach Me queue', async ({ page, app }) => {
    const row = await uploadThroughThePage(page)
    await row.getByRole('button', { name: 'Categorize' }).click()
    await expect(row).toContainText('Full')

    // Whatever the model was not sure about waits for the user.
    const queue = await app.api.get('/annotations/review-queue')
    expect(queue.length, 'rows waiting in the queue').toBeGreaterThan(0)
    await open(page, '/review')
    await expect(page.getByText(new RegExp(`0\\s*/\\s*${queue.length}\\b`))).toBeVisible()
  })

  test('sorting starts by itself when the statement lands', async ({ page }) => {
    expectedFailure('R2 item 4: categorisation waits for a click on Categorize after every upload')
    const row = await uploadThroughThePage(page)
    await expect(row).toContainText('Full', { timeout: 5000 })
  })
})
