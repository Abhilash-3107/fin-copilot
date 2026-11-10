import { describe, it, expect, vi, afterEach } from 'vitest'
import { pollAnnotationJob, runAnnotationJob, JobBusyError, MAX_POLL_FAILURES } from './api.js'

function respond(status, body) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
}

afterEach(() => vi.unstubAllGlobals())

describe('pollAnnotationJob', () => {
  it('rides out a transient 500 instead of failing the job', async () => {
    const fetch = vi.fn()
      .mockReturnValueOnce(respond(200, { status: 'running', processed: 65, total: 101 }))
      .mockReturnValueOnce(respond(500, { detail: 'database is locked' }))
      .mockReturnValueOnce(respond(200, { status: 'completed', result: { total_processed: 101 } }))
    vi.stubGlobal('fetch', fetch)

    const seen = []
    const result = await pollAnnotationJob('j1', job => seen.push(job.status), 0)
    expect(result).toEqual({ total_processed: 101 })
    expect(seen).toEqual(['running', 'completed'])
  })

  it('gives up after repeated consecutive failures', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(500, { detail: 'boom' })))
    await expect(pollAnnotationJob('j1', null, 0)).rejects.toMatchObject({ status: 500 })
    expect(fetch).toHaveBeenCalledTimes(MAX_POLL_FAILURES)
  })

  it('fails fast on a 404 (job gone)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(404, { detail: 'Job not found' })))
    await expect(pollAnnotationJob('j1', null, 0)).rejects.toMatchObject({ status: 404 })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('still rejects when the job itself fails', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(200, { status: 'failed', error: 'LLM down' })))
    await expect(pollAnnotationJob('j1', null, 0)).rejects.toThrow('LLM down')
  })
})

describe('runAnnotationJob', () => {
  const attached = extra => respond(202, { job_id: 'other', status: 'running', attached: true, ...extra })

  it("refuses to report a re-embed job's result as this run", async () => {
    vi.stubGlobal('fetch', vi.fn(() => attached({ kind: 'reembed', statement_id: null })))
    const run = runAnnotationJob({ statement_id: 's1' }, null, 0)
    await expect(run).rejects.toBeInstanceOf(JobBusyError)
    await expect(runAnnotationJob({ statement_id: 's1' }, null, 0)).rejects.toThrow("memory is being rebuilt")
    expect(fetch).toHaveBeenCalledTimes(2) // never polled the other job
  })

  it('refuses a re-embed even when the scope matches (Dashboard sorts everything: {})', async () => {
    // Same statement_id (null) as the request, so only the kind check stops it.
    vi.stubGlobal('fetch', vi.fn(() => attached({ kind: 'reembed', statement_id: null })))
    await expect(runAnnotationJob({}, null, 0)).rejects.toBeInstanceOf(JobBusyError)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it("refuses another statement's run", async () => {
    vi.stubGlobal('fetch', vi.fn(() => attached({ kind: 'annotate', statement_id: 's2' })))
    await expect(runAnnotationJob({ statement_id: 's1' }, null, 0))
      .rejects.toThrow('Another categorization run is in progress')
  })

  it('re-attaches to the same run (a double-click, a second tab)', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockReturnValueOnce(attached({ kind: 'annotate', statement_id: 's1' }))
      .mockReturnValueOnce(respond(200, { status: 'completed', result: { total_processed: 3 } })))
    await expect(runAnnotationJob({ statement_id: 's1' }, null, 0)).resolves.toEqual({ total_processed: 3 })
  })

  it('polls a freshly started job', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockReturnValueOnce(respond(202, { job_id: 'new', status: 'queued' }))
      .mockReturnValueOnce(respond(200, { status: 'completed', result: { total_processed: 1 } })))
    await expect(runAnnotationJob({}, null, 0)).resolves.toEqual({ total_processed: 1 })
  })
})
