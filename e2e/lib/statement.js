import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

/** The statement the parser tests use: 8 rows, balanced, October 2026. */
export const FIXTURE_PDF = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement.pdf')

/** The same bank and account a month later: 8 rows, balanced, opens where October closes. */
export const FIXTURE_PDF_NOV = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_nov.pdf')

/** A second account at another bank: October 2026, six payments, balanced; its header prints the whole month. */
export const FIXTURE_PDF_OTHER_BANK = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_other_bank.pdf')

export function uploadFixture(api) {
  return api.upload('/statements/upload', 'sample_statement.pdf', readFileSync(FIXTURE_PDF), 'application/pdf')
}

/**
 * Wait for the sorting job the server queued for a statement when it landed,
 * and return its result. Nothing here starts a run: sorting begins by itself.
 */
export async function sortedByServer(api, statementId) {
  for (let i = 0; i < 300; i++) {
    const jobs = await api.get('/annotations/jobs')
    const job = jobs.find((j) => j.statement_id === statementId)
    if (job?.status === 'completed') return job.result
    if (job?.status === 'failed') throw new Error(`Sorting failed: ${job.error}`)
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error('Sorting did not finish in 30s')
}
