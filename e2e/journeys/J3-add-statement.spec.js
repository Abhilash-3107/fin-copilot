// J3 - Add statements. Drop the fixture statements, see the balance check pass,
// see sorting start by itself, and follow the way on to Teach Me.
import { readFileSync } from 'node:fs'
import { test, expect } from '../fixtures.js'
import { open } from '../lib/ui.js'
import { FIXTURE_PDF, FIXTURE_PDF_NOV } from '../lib/statement.js'

async function drop(page, files) {
  await open(page, '/upload')
  await page.locator('input[type=file]').setInputFiles(files)
  await page.getByRole('button', { name: 'Upload', exact: true }).click()
}

const asUpload = (name, path) => ({ name, mimeType: 'application/pdf', buffer: readFileSync(path) })
const fileRow = (page, name) => page.getByRole('listitem').filter({ hasText: name })
const statementRows = (page) => page.getByRole('row').filter({ hasText: 'generic' })

test.describe('J3 add a statement', () => {
  test('the page says what it supports before the first upload', async ({ page }) => {
    await open(page, '/upload')
    await expect(page.getByText(/Works with Kotak statements/)).toBeVisible()
    await expect(page.getByText(/running-balance column/)).toBeVisible()
    await expect(page.getByText(/Password-protected PDFs work/)).toBeVisible()
    await expect(page.getByText(/Your statement stays on this computer/)).toBeVisible()
    await expect(page.getByText(/sorting starts by itself/)).toBeVisible()
  })

  test('an uploaded statement shows its balance check passing', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const item = fileRow(page, 'sample_statement.pdf')
    await expect(item).toContainText('8 payments added')
    await expect(item).toContainText('Balance checks out')

    const [statement] = await app.api.get('/statements')
    expect(statement.balance_check.balanced).toBe(true)
    expect(statement.balance_check.checked).toBeGreaterThan(0)
    const { consistent, checked } = statement.balance_check
    await expect(statementRows(page)).toContainText(`${consistent}/${checked}`)
  })

  test('sorting starts by itself when the statement lands', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    // No click on anything after Upload.
    await expect(page.getByRole('status')).toContainText(/Finished: \d+ sorted/)

    // The stub, not some other model, did the work.
    const chats = app.stub.requests.filter((r) => r.path === '/api/chat')
    expect(chats.length, 'chat calls the model stub received').toBeGreaterThan(0)
    const [job] = await app.api.get('/annotations/jobs')
    expect(job.status).toBe('completed')
    expect(job.result.ai_active, 'the run reports the model as active').toBe(true)

    // The table counts "sorted" the way Teach Me does: what it is not waiting on.
    const [statement] = await app.api.get('/statements')
    const queue = await app.api.get('/annotations/review-queue')
    expect(statement.sorted_count + queue.length).toBe(statement.txn_count)
    await expect(statementRows(page)).toContainText(`${statement.sorted_count}/${statement.txn_count}`)
  })

  test('the page says what happened and leads to Teach Me, where the rest waits', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const summary = page.getByRole('status')
    await expect(summary).toContainText(/Finished: \d+ sorted, \d+ needs? you/)

    // Whatever the model was not sure about waits for the user.
    const queue = await app.api.get('/annotations/review-queue')
    expect(queue.length, 'rows waiting in the queue').toBeGreaterThan(0)
    await summary.getByRole('link', { name: /Teach Me/ }).click()
    await expect(page.getByText(new RegExp(`0\\s*/\\s*${queue.length}\\b`))).toBeVisible()
  })

  test('two statements dropped together go in one after the other, each with its own result', async ({ page, app }) => {
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_NOV])

    for (const name of ['sample_statement.pdf', 'sample_statement_nov.pdf']) {
      const item = fileRow(page, name)
      await expect(item).toContainText('8 payments added')
      await expect(item).toContainText(/\d+ sorted, \d+ needs? you/)
    }
    await expect(statementRows(page)).toHaveCount(2)
    await expect(page.getByRole('status')).toContainText(/Finished: \d+ sorted/)

    // Each statement was sorted by a job of its own, one at a time.
    const statements = await app.api.get('/statements')
    const jobs = await app.api.get('/annotations/jobs')
    for (const statement of statements) {
      const job = jobs.find((j) => j.statement_id === statement.id)
      expect(job?.status, `the job for ${statement.statement_month}`).toBe('completed')
      // "Sorted" in the table is what Teach Me is not waiting on.
      expect(statement.sorted_count).toBeLessThanOrEqual(statement.txn_count - job.result.llm_failed)
    }
  })

  test('a statement dropped while another is being sorted waits its turn, is not reaped, and then runs', async ({ page, app }) => {
    app.stub.hold()
    await drop(page, [FIXTURE_PDF, FIXTURE_PDF_NOV])
    const first = fileRow(page, 'sample_statement.pdf')
    const second = fileRow(page, 'sample_statement_nov.pdf')

    // The first job is stuck on the model; the second waits in line behind it.
    await expect(second).toContainText('Waiting to sort')
    await expect(first).toContainText(/Sorting \d+ of \d+|Sorting/)
    const active = await app.api.get('/annotations/jobs?active=1')
    expect(active.map((j) => j.status)).toEqual(['running', 'queued'])

    // However long it waits, it is waiting, not abandoned.
    app.stub.release()
    await expect(page.getByRole('status')).toContainText(/Finished: \d+ sorted/)
    const jobs = (await app.api.get('/annotations/jobs')).reverse()
    expect(jobs.map((j) => j.status)).toEqual(['completed', 'completed'])
    // Oldest statement first, each exactly once.
    const statements = await app.api.get('/statements')
    const month = (id) => statements.find((s) => s.id === id).statement_month
    expect(jobs.map((j) => month(j.statement_id))).toEqual(['2026-10', '2026-11'])
  })

  test('a file that is not a PDF is refused up front and the PDF beside it still goes in', async ({ page, app }) => {
    await open(page, '/upload')
    await page.locator('input[type=file]').setInputFiles([
      { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not a statement') },
      asUpload('sample_statement.pdf', FIXTURE_PDF),
    ])
    await expect(page.getByRole('alert')).toContainText("notes.txt isn't a PDF")
    await expect(fileRow(page, 'notes.txt')).toHaveCount(0)

    await page.getByRole('button', { name: 'Upload', exact: true }).click()
    await expect(fileRow(page, 'sample_statement.pdf')).toContainText('8 payments added')
    expect(await app.api.get('/statements')).toHaveLength(1)
  })

  test('one bad file does not lose the others', async ({ page, app, tolerate }) => {
    await tolerate(/\/api\/statements\/upload/, 422)
    // A PDF the parser cannot read: right bytes at the front, no table inside.
    const blank = { name: 'blank.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n') }
    await open(page, '/upload')
    await page.locator('input[type=file]').setInputFiles([blank, asUpload('sample_statement.pdf', FIXTURE_PDF)])
    await page.getByRole('button', { name: 'Upload', exact: true }).click()

    await expect(fileRow(page, 'sample_statement.pdf')).toContainText('8 payments added')
    await expect(fileRow(page, 'blank.pdf')).toContainText("couldn't find a transaction table")
    expect(await app.api.get('/statements')).toHaveLength(1)
  })
})

test.describe('J3 with no model connected', () => {
  test.use({ modelMode: 'none' })

  test('the statement is still sorted by rules and the rest is left for you, with the way to connect a model', async ({ page, app }) => {
    await drop(page, FIXTURE_PDF)
    const summary = page.getByRole('status')
    await expect(summary).toContainText(/Finished: \d+ sorted, [1-9]\d* needs? you/)
    await expect(summary).toContainText('No model is connected')
    await expect(summary.getByRole('link', { name: /Settings › AI & Model/ })).toBeVisible()

    const unsorted = await app.api.get('/transactions?unannotated=true')
    expect(unsorted.length, 'rows nobody decided').toBeGreaterThan(0)
    await summary.getByRole('link', { name: /Teach Me/ }).click()
    await expect(page.getByText(new RegExp(`0\\s*/\\s*\\d+`))).toBeVisible()
  })
})
