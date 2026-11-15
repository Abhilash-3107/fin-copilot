// J8 - Backup, delete, restore. The restored ledger must match the original.
import { test, expect } from '../fixtures.js'
import { storedFacts } from '../lib/snapshot.js'
import { categorise, uploadFixture } from '../lib/statement.js'
import { figureNear, open } from '../lib/ui.js'

async function openDataSettings(page) {
  await open(page, '/settings')
  await page.getByRole('button', { name: 'Data' }).click()
}

test('a backup restores the ledger exactly after everything is deleted', async ({ page, app }, testInfo) => {
  // A ledger with a little of everything: demo history, a real statement with
  // machine answers, and things the user adds by hand.
  await app.loadDemo()
  const statement = await uploadFixture(app.api)
  await categorise(app.api, statement.id)
  await app.api.post('/people', { name: 'E2E Friend', upi: 'e2e.friend@upi', relationship: 'friend' })
  await app.api.post('/rules', { pattern: 'bookstore', category: 'Shopping', subcategory: 'General Retail' })
  await app.api.put('/config', { confidence_threshold: 0.7 })

  const original = await storedFacts(app.api)
  expect(original.transactions.length).toBeGreaterThan(150)
  expect(original.statements.length).toBeGreaterThan(1)
  await open(page, '/dashboard')
  const keptBefore = await figureNear(page, 'Kept')

  // Back up.
  await openDataSettings(page)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download backup' }).click()
  const backupPath = testInfo.outputPath('backup.db')
  await (await download).saveAs(backupPath)

  // Delete everything.
  await page.getByRole('button', { name: 'Delete all data' }).click()
  await page.getByRole('button', { name: 'Delete everything' }).click()
  await expect.poll(async () => (await app.api.get('/statements')).length).toBe(0)
  expect((await storedFacts(app.api)).transactions).toEqual([])

  // Restore.
  await openDataSettings(page)
  await page.getByLabel('Backup file to restore').setInputFiles(backupPath)
  await page.getByRole('button', { name: 'Replace and restore' }).click()
  await expect(page.getByText(/^Restored \d+ transactions? from/)).toBeVisible()

  expect(await storedFacts(app.api)).toEqual(original)
  await open(page, '/dashboard')
  expect(await figureNear(page, 'Kept')).toBe(keptBefore)
})
