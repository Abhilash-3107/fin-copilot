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

/**
 * The same account for 5 October to 15 November: the last four October payments and all of
 * November with the balances the two months carry, then three payments after November's last.
 * Added after both months it brings three; added first, the months bring what it lacks.
 */
export const FIXTURE_PDF_OVERLAP = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_overlap.pdf')

/**
 * Two accounts of one person (October 2026, two fictional banks). Rs 12,000 leaves the first on the
 * 14th with "TO OWN ACCOUNT XX1234" and lands in the second on the 15th: it pairs by itself.
 * Rs 7,000 leaves on the 20th and lands on the 21st with nothing that says it is their own money:
 * it does not pair, and Teach Me offers the candidate.
 */
export const FIXTURE_PDF_TRANSFER_OUT = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_transfer_out.pdf')
export const FIXTURE_PDF_TRANSFER_IN = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_transfer_in.pdf')

export function uploadFixture(api) {
  return api.upload('/statements/upload', 'sample_statement.pdf', readFileSync(FIXTURE_PDF), 'application/pdf')
}

export function uploadFixtureNov(api) {
  return api.upload('/statements/upload', 'sample_statement_nov.pdf', readFileSync(FIXTURE_PDF_NOV), 'application/pdf')
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

/**
 * Add the two transfer statements the way the Add Statement page does and wait until the server has
 * finished sorting both. Their 12,000 pair pairs by itself; their 7,000 pair has no sign, so it waits.
 */
export async function uploadTransferFixtures(api) {
  const out = await api.upload('/statements/upload', 'sample_statement_transfer_out.pdf', readFileSync(FIXTURE_PDF_TRANSFER_OUT), 'application/pdf')
  await sortedByServer(api, out.id)
  const inn = await api.upload('/statements/upload', 'sample_statement_transfer_in.pdf', readFileSync(FIXTURE_PDF_TRANSFER_IN), 'application/pdf')
  await sortedByServer(api, inn.id)
}
