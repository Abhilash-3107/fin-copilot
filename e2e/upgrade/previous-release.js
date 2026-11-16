// What only the previous published release needs. 0.1.2 does not sort a
// statement when it lands: the journey that builds its ledger has to ask, the
// way its Categorize button did. The release under test sorts by itself, so
// nothing in lib/ starts a run.

/** Start a sorting run for a statement on the previous release and wait for the job to finish. */
export async function categorise(api, statementId) {
  const { job_id: jobId } = await api.post('/annotations/auto-annotate/jobs', { statement_id: statementId })
  for (let i = 0; i < 600; i++) {
    const job = await api.get(`/annotations/jobs/${jobId}`)
    if (job.status === 'completed') return job.result
    if (job.status === 'failed') throw new Error(`Categorisation failed: ${job.error}`)
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error('Categorisation did not finish in 60s')
}
