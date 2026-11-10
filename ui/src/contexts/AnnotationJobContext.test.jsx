import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

const { api } = vi.hoisted(() => ({
  api: { getActiveAnnotationJob: vi.fn(), pollAnnotationJob: vi.fn(), runAnnotationJob: vi.fn() },
}))
vi.mock('../lib/api.js', async importOriginal => ({ ...(await importOriginal()), ...api }))

import { AnnotationJobProvider, useAnnotationJob } from './AnnotationJobContext.jsx'
import { JobBusyError } from '../lib/api.js'

const wrapper = ({ children }) => <AnnotationJobProvider>{children}</AnnotationJobProvider>

describe('AnnotationJobProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getActiveAnnotationJob.mockResolvedValue(null)
  })

  it('refuses a second run while one is tracked, without touching its card', async () => {
    let finish
    api.runAnnotationJob.mockImplementation((_body, onProgress) => {
      onProgress({ status: 'running', processed: 1, total: 4 })
      return new Promise(r => { finish = r })
    })
    const { result } = renderHook(() => useAnnotationJob(), { wrapper })

    let first
    act(() => { first = result.current.startAnnotation({ statement_id: 's1' }) })
    await waitFor(() => expect(result.current.job?.status).toBe('running'))

    await expect(result.current.startAnnotation({ statement_id: 's2' })).rejects.toBeInstanceOf(JobBusyError)
    expect(api.runAnnotationJob).toHaveBeenCalledTimes(1)
    expect(result.current.job?.processed).toBe(1) // first run's card still up

    await act(async () => { finish({ total_processed: 4 }); await first })
    expect(result.current.job).toBeNull()
  })

  it('refuses a run while a re-attached job (e.g. after reload) is tracked', async () => {
    api.getActiveAnnotationJob.mockResolvedValue({ id: 're', status: 'running', kind: 'reembed' })
    api.pollAnnotationJob.mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() => useAnnotationJob(), { wrapper })
    await waitFor(() => expect(result.current.job?.kind).toBe('reembed'))

    await expect(result.current.startAnnotation({})).rejects.toThrow("memory is being rebuilt")
    expect(api.runAnnotationJob).not.toHaveBeenCalled()
  })
})
