import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

/** The statement the parser tests use: 8 rows, balanced, October 2026. */
export const FIXTURE_PDF = path.resolve(here, '../../finance_copilot/tests/fixtures/sample_statement.pdf')

export function uploadFixture(api) {
  return api.upload('/statements/upload', 'sample_statement.pdf', readFileSync(FIXTURE_PDF), 'application/pdf')
}

/** Run categorisation for a statement and wait for the job to finish. */
export async function categorise(api, statementId) {
  const { job_id: jobId } = await api.post('/annotations/auto-annotate/jobs', { statement_id: statementId })
  for (let i = 0; i < 200; i++) {
    const job = await api.get(`/annotations/jobs/${jobId}`)
    if (job.status === 'completed') return job.result
    if (job.status === 'failed') throw new Error(`Categorisation failed: ${job.error}`)
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error('Categorisation did not finish in 20s')
}
