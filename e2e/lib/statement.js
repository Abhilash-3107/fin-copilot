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
/**
 * A second export of the first of those accounts: 14 to 31 October, so it holds the signed
 * 12,000 transfer, the 7,000 and the electricity bill again, and brings one payment the month's
 * file did not have (28 October). Added after the month it adds that one payment and no second
 * copy of the transfer.
 */
export const FIXTURE_PDF_TRANSFER_OUT_LATE = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_transfer_out_late.pdf')

/**
 * Two more accounts of one person (October 2026, SBI and Kotak). Rs 25,000 leaves the first on the
 * 12th as "NEFT/N123456789/ABHI B/SBIN0001234" and lands in the second the same day as
 * "NEFT/N123456789/ABHI B/KKBK0000123". The merchant catalog labels a NEFT line Transfers / Peer
 * Transfer, so both sides carry a label, nothing says the money is their own and nothing pairs.
 */
export const FIXTURE_PDF_NEFT_OUT = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_neft_out.pdf')
export const FIXTURE_PDF_NEFT_IN = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement_neft_in.pdf')

/**
 * The same fictional accounts as spreadsheets (made by scripts/make_spreadsheet_fixtures.py). The
 * October and November CSVs name the account number the PDFs print, so they land beside the PDFs.
 * The overlap CSV is 5 October to 15 November with the narrations cut to 18 characters and the
 * balances kept. The export CSVs are another account whose header no keyword names ("When",
 * "What", "Moved", "Left"), with nothing above the table: the first asks what the columns are.
 */
const fixture = (name) => path.resolve(here, `../../finance_copilot/tests/fixtures/${name}`)
export const FIXTURE_CSV = fixture('sample_statement.csv')
export const FIXTURE_CSV_NOV = fixture('sample_statement_nov.csv')
export const FIXTURE_CSV_OVERLAP = fixture('sample_statement_overlap.csv')
export const FIXTURE_CSV_EXPORT_OCT = fixture('sample_export_oct.csv')
export const FIXTURE_CSV_EXPORT_NOV = fixture('sample_export_nov.csv')
export const FIXTURE_XLSX = fixture('sample_statement.xlsx')
export const FIXTURE_XLS = fixture('sample_statement.xls')

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

/**
 * Add the two NEFT statements the way the Add Statement page does and wait until the server has
 * finished sorting both. Their 25,000 pair has no sign and the merchant catalog has labelled both
 * sides Transfers / Peer Transfer, so it waits for an answer.
 */
export async function uploadNeftFixtures(api) {
  const out = await api.upload('/statements/upload', 'sample_statement_neft_out.pdf', readFileSync(FIXTURE_PDF_NEFT_OUT), 'application/pdf')
  await sortedByServer(api, out.id)
  const inn = await api.upload('/statements/upload', 'sample_statement_neft_in.pdf', readFileSync(FIXTURE_PDF_NEFT_IN), 'application/pdf')
  await sortedByServer(api, inn.id)
}
