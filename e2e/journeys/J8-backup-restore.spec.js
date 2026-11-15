// J8 - Backup, delete, restore. The restored ledger must match the original.
import { test, expect } from '../fixtures.js'
import { storedFacts } from '../lib/snapshot.js'
import { categorise, uploadFixture } from '../lib/statement.js'
import { figureNear, open, openSettingsTab } from '../lib/ui.js'

const openDataSettings = (page) => openSettingsTab(page, 'Data')

/**
 * A ledger with a little of everything, built the way a user builds one: a real
 * statement with machine answers, then what they add by hand. Demo data is not
 * used, because the app refuses a statement while sample data is loaded.
 */
async function buildRichLedger(api) {
  const statement = await uploadFixture(api)
  await categorise(api, statement.id)
  const person = await api.post('/people', { name: 'E2E Friend', upi: 'e2e.friend@upi', relationship: 'friend' })
  const rule = await api.post('/rules', { pattern: 'bookstore', category: 'Shopping', subcategory: 'General Retail' })
  await api.post(`/rules/${rule.id}/apply`)
  await api.put('/config', { confidence_threshold: 0.7 })

  // A restore re-embeds in the background and then re-scores the queue, which
  // settles anything a rule can now decide. Settle it here, so the ledger
  // being backed up is the one the restore will end on.
  await api.post('/annotations/review-queue/rescore')
  const queue = await api.get('/annotations/review-queue')
  expect(queue.length, 'rows waiting for review').toBeGreaterThan(0)
  const rows = (await api.get('/transactions?include=annotation')).filter((t) => t.annotation_id)
  const relabel = rows.find((t) => !queue.some((q) => q.id === t.annotation_id))
  expect(relabel, 'a confidently labelled row to relabel by hand').toBeDefined()

  // A manual relabel, a skipped review item and a group with members.
  await api.patch(`/annotations/${relabel.annotation_id}`, { category: 'Shopping', subcategory: 'General Retail' })
  await api.post(`/annotations/${queue[0].id}/skip`)
  const group = await api.post('/groups', { name: 'E2E dinner', note: 'Split three ways' })
  const [first, second] = rows.filter((t) => t.debit_credit === 'debit')
  await api.post(`/groups/${group.id}/members`, { transaction_id: first.id, people: [person.id], txn_type: 'split' })
  await api.post(`/groups/${group.id}/members`, { transaction_id: second.id, people: [], txn_type: 'event' })
}

test('a backup restores the ledger exactly after everything is deleted', async ({ page, app }, testInfo) => {
  await buildRichLedger(app.api)

  const original = await storedFacts(app.api)
  expect(original.transactions.length).toBeGreaterThan(5)
  // The facts being compared are rich enough for the comparison to mean something.
  expect(original.people).toHaveLength(1)
  expect(original.rules).toHaveLength(1)
  expect(original.groups[0].members).toHaveLength(2)
  expect(original.snoozedCount).toBe(1)
  expect(original.transactions.some((t) => t.source === 'manual')).toBe(true)
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
  await page.getByRole('dialog').getByRole('button', { name: 'Delete everything' }).click()
  await expect.poll(async () => (await app.api.get('/statements')).length).toBe(0)
  expect((await storedFacts(app.api)).transactions).toEqual([])

  // Restore.
  await openDataSettings(page)
  await page.getByLabel('Backup file to restore').setInputFiles(backupPath)
  const restored = page.waitForResponse((r) => r.url().endsWith('/api/data/restore'))
  await page.getByRole('button', { name: 'Replace and restore' }).click()
  expect((await restored).ok(), 'restore request succeeded').toBe(true)
  // The app reloads after a restore and says what it did once it is back.
  await expect(page.getByText(/^Restored \d+ transactions? from/)).toBeVisible()

  expect(await storedFacts(app.api)).toEqual(original)
  await open(page, '/dashboard')
  expect(await figureNear(page, 'Kept')).toBe(keptBefore)
})
