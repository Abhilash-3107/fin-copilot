import { describe, it, expect, vi, afterEach } from 'vitest'
import { pollAnnotationJob, MAX_POLL_FAILURES } from './api.js'

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
