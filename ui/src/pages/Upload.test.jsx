import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { HashRouter } from 'react-router-dom'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))
const { pollJob } = vi.hoisted(() => ({ pollJob: vi.fn() }))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {}, pollAnnotationJob: pollJob }))
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => noopToast,
}))
const { jobCtx } = vi.hoisted(() => ({ jobCtx: { job: null, startAnnotation: null, watchJobs: null } }))
vi.mock('../contexts/AnnotationJobContext.jsx', () => ({
  useAnnotationJob: () => jobCtx,
}))

const { demoCtx } = vi.hoisted(() => ({ demoCtx: { sample: false, removable: false, mixed: false, hosted: false, refresh: vi.fn() } }))
vi.mock('../contexts/DemoDataContext.jsx', () => ({
  useDemoData: () => demoCtx,
}))

const stmt = {
  id: 's1', bank_name: 'kotak', statement_month: '2026-07', balance_check: null,
  txn_count: 10, annotated_count: 2, embedded_count: 10, uploaded_at: '2026-07-01T10:00:00',
}

const uploaded = (id, extra = {}) => ({
  id, bank_name: 'kotak', statement_month: '2026-07', warnings: [],
  transaction_count: 8, balance_check: { checked: 7, consistent: 7, balanced: true },
  sorting_job_id: `job-${id}`, ...extra,
})
// 8 rows: 5 by rules, 2 from history, 1 left undecided (no model).
const SORTED = {
  total_processed: 8, rule_matched: 5, rag_direct_annotated: 2, llm_annotated: 0,
  llm_failed: 1, low_confidence: 0, already_annotated: 0, ai_active: false, llm_error: null,
}

import Upload from './Upload.jsx'

const renderUpload = () => render(<HashRouter><Upload /></HashRouter>)

const pdf = () => new File(['%PDF-1.4'], 'statement.pdf', { type: 'application/pdf' })

describe('Upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    jobCtx.job = null
    jobCtx.startAnnotation = vi.fn()
    jobCtx.watchJobs = vi.fn()
    pollJob.mockResolvedValue(SORTED)
    Object.assign(demoCtx, { sample: false, removable: false, mixed: false, hosted: false })
    mockApi.get.mockResolvedValue([])
    mockApi.upload.mockResolvedValue(uploaded('s1'))
  })

  it('clears the file input after a pick, so the same file can be picked again', async () => {
    const { container } = renderUpload()
    const input = container.querySelector('input[type=file]')
    // jsdom does not model a file input's path value, so record what the page
    // writes to it. A browser only fires "change" when that value differs, so
    // a stale path would swallow a re-pick of the same file after upload,
    // delete or Cancel - which is exactly what users hit.
    const writes = []
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: () => '',
      set: v => { writes.push(v) },
    })

    fireEvent.change(input, { target: { files: [pdf()] } })
    expect(screen.getByText('statement.pdf')).toBeInTheDocument()
    expect(writes).toEqual([''])

    fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
    await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(1))
    await screen.findByText('7 sorted, 1 needs you')

    fireEvent.change(input, { target: { files: [pdf()] } })
    expect(writes).toEqual(['', ''])
    expect(screen.getByRole('button', { name: 'Upload' })).toBeInTheDocument()
  })

  it("Sort again runs through the job flow and shows that run's result", async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, rag_direct_annotated: 1, llm_annotated: 2,
      llm_failed: 0, low_confidence: 0, already_annotated: 2, ai_active: true, llm_error: null,
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    await waitFor(() => expect(jobCtx.startAnnotation).toHaveBeenCalledWith({ statement_id: 's1' }))
    expect(await screen.findByText('Figured out by AI: 2')).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/auto-annotate', expect.anything())
  })

  it('every Sort again waits while another run is tracked', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.job = { status: 'running', kind: 'reembed' }
    renderUpload()
    expect(await screen.findByRole('button', { name: /sort again/i })).toBeDisabled()
  })

  it('shows the provider error in the results panel', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: true, llm_error: 'provider refused the request (HTTP 404)',
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    expect(await screen.findByText(/isn.t available \(provider refused the request \(HTTP 404\)\)/))
      .toBeInTheDocument()
  })

  // The page lives under a HashRouter, so a plain href="/settings" opens the
  // dashboard; the links must go through the router and name the tab.
  it('links to the AI & Model tab of Settings through the router', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: true, llm_error: 'provider refused the request (HTTP 404)',
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    const link = await screen.findByRole('link', { name: /Settings › AI & Model/ })
    expect(link.getAttribute('href')).toBe('#/settings?tab=ai')
  })

  it('links to the AI & Model tab when AI is off', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: false, llm_error: null,
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /sort again/i }))
    const link = await screen.findByRole('link', { name: /Settings › AI & Model/ })
    expect(link.getAttribute('href')).toBe('#/settings?tab=ai')
  })

  it('tells you to remove sample data before adding a statement, and points at the bar', async () => {
    Object.assign(demoCtx, { sample: true, removable: true })
    const { container } = renderUpload()
    expect(await screen.findByText(/still has sample data/)).toBeInTheDocument()
    // One remove action per screen: the bar's. The notice only explains.
    expect(screen.queryByRole('button', { name: 'Remove sample data' })).not.toBeInTheDocument()
    expect(container.querySelector('.pointer-events-none')).not.toBeNull()
  })

  it('shows no sample-data notice on a ledger without it', async () => {
    renderUpload()
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
    expect(screen.queryByText(/still has sample data/)).not.toBeInTheDocument()
  })

  it('on the hosted demo does not tell you to remove sample data, and leaves the dropzone to the API', async () => {
    Object.assign(demoCtx, { sample: true, removable: false, hosted: true })
    const { container } = renderUpload()
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
    expect(screen.queryByText(/still has sample data/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove sample data' })).not.toBeInTheDocument()
    expect(container.querySelector('.pointer-events-none')).toBeNull()
  })

  it('refreshes the sample-data status after deleting a statement', async () => {
    mockApi.get.mockResolvedValue([stmt])
    mockApi.delete.mockResolvedValue({})
    const { container } = renderUpload()
    await screen.findByText('kotak')
    fireEvent.click(container.querySelector('svg.lucide-trash2').closest('button'))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/statements/s1'))
    await waitFor(() => expect(demoCtx.refresh).toHaveBeenCalledTimes(1))
  })

  it('refreshes the sample-data status after resetting a statement', async () => {
    mockApi.get.mockResolvedValue([stmt])
    mockApi.delete.mockResolvedValue({})
    const { container } = renderUpload()
    await screen.findByText('kotak')
    fireEvent.click(container.querySelector('svg.lucide-rotate-ccw').closest('button'))
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/statements/s1/data'))
    await waitFor(() => expect(demoCtx.refresh).toHaveBeenCalledTimes(1))
  })

  describe('before the first upload', () => {
    it('says which banks work, what happens with a locked PDF, where the file stays and what happens next', async () => {
      renderUpload()
      await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
      expect(screen.getByText(/Kotak statements/)).toBeInTheDocument()
      expect(screen.getByText(/running-balance column/)).toBeInTheDocument()
      expect(screen.getByText(/password-protected PDFs work/i)).toBeInTheDocument()
      expect(screen.getByText(/stay on this computer/)).toBeInTheDocument()
      expect(screen.getByText(/balance is checked, then sorting starts by itself/i)).toBeInTheDocument()
    })

    it('leaves the hosted demo to its own message', async () => {
      Object.assign(demoCtx, { sample: true, hosted: true })
      renderUpload()
      await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
      expect(screen.queryByText(/Kotak statements/)).not.toBeInTheDocument()
    })

    it('gives way to the files once some are chosen', async () => {
      const { container } = renderUpload()
      await screen.findByText(/No statements yet/)
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      expect(screen.queryByText(/Kotak statements/)).not.toBeInTheDocument()
    })
  })

  describe('several files', () => {
    const named = name => new File(['%PDF-1.4'], name, { type: 'application/pdf' })

    it('lets the picker take several files', async () => {
      const { container } = renderUpload()
      await screen.findByText(/No statements yet/)
      expect(container.querySelector('input[type=file]').multiple).toBe(true)
    })

    it('uploads them one after another, each with its own result and a sorted summary', async () => {
      mockApi.upload
        .mockResolvedValueOnce(uploaded('s1'))
        .mockResolvedValueOnce(uploaded('s2', { bank_name: 'hdfc' }))
      pollJob
        .mockResolvedValueOnce(SORTED)
        .mockResolvedValueOnce({ ...SORTED, rule_matched: 6, rag_direct_annotated: 2, llm_failed: 0 })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [named('a.pdf'), named('b.pdf')] } })
      expect(screen.getByText('a.pdf')).toBeInTheDocument()
      expect(screen.getByText('b.pdf')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))

      expect(await screen.findByText('7 sorted, 1 needs you')).toBeInTheDocument()
      expect(await screen.findByText('8 sorted, 0 need you')).toBeInTheDocument()
      expect(mockApi.upload).toHaveBeenCalledTimes(2)
      expect(mockApi.upload.mock.calls[0][1].get('file').name).toBe('a.pdf')
      expect(mockApi.upload.mock.calls[1][1].get('file').name).toBe('b.pdf')
      // Each row reports what went in and the balance check.
      expect(screen.getAllByText(/8 payments added/)).toHaveLength(2)
      expect(screen.getAllByText(/Balance checks out/)).toHaveLength(2)
      // One account of the whole drop, with the way on.
      expect(screen.getByRole('status')).toHaveTextContent('Finished: 15 sorted, 1 needs you')
      expect(screen.getByRole('link', { name: /Teach Me/ }).getAttribute('href')).toBe('#/review')
    })

    it('keeps going when one file fails, says why, and lets that file be retried', async () => {
      mockApi.upload
        .mockRejectedValueOnce(new Error('This PDF is password-protected. Enter its password and upload again.'))
        .mockResolvedValueOnce(uploaded('s2'))
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [named('locked.pdf'), named('open.pdf')] } })
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))

      expect(await screen.findByText(/password-protected/)).toBeInTheDocument()
      expect(await screen.findByText('7 sorted, 1 needs you')).toBeInTheDocument()
      expect(mockApi.upload).toHaveBeenCalledTimes(2)

      // Enter the password and upload again: only the failed file goes up.
      mockApi.upload.mockResolvedValueOnce(uploaded('s1'))
      fireEvent.change(screen.getByPlaceholderText(/Password/), { target: { value: 'hunter2' } })
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
      await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(3))
      const retry = mockApi.upload.mock.calls[2][1]
      expect(retry.get('file').name).toBe('locked.pdf')
      expect(retry.get('password')).toBe('hunter2')
    })

    it('refuses a file that is not a PDF before anything is uploaded', async () => {
      const { container } = renderUpload()
      const txt = new File(['hello'], 'notes.txt', { type: 'text/plain' })
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [txt, named('ok.pdf')] } })

      expect(screen.getByRole('alert')).toHaveTextContent("notes.txt isn't a PDF")
      expect(screen.queryByText('notes.txt', { selector: 'span' })).not.toBeInTheDocument()
      // The PDF next to it is still taken.
      expect(screen.getByText('ok.pdf')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
      await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(1))
    })

    it('refuses a dropped non-PDF the same way', async () => {
      const { container } = renderUpload()
      await screen.findByText(/No statements yet/)
      const zone = container.querySelector('input[type=file]').parentElement
      const txt = new File(['hello'], 'notes.txt', { type: 'text/plain' })
      fireEvent.drop(zone, { dataTransfer: { files: [txt] } })
      expect(screen.getByRole('alert')).toHaveTextContent("notes.txt isn't a PDF")
      expect(mockApi.upload).not.toHaveBeenCalled()
    })
  })

  describe('sorting starts by itself', () => {
    it('shows progress and then the outcome without a click on anything else', async () => {
      let finish
      pollJob.mockImplementation((_id, onProgress) => {
        onProgress({ status: 'running', processed: 3, total: 8 })
        return new Promise(r => { finish = r })
      })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))

      expect(await screen.findByText('Sorting 3 of 8')).toBeInTheDocument()
      // The floating progress card is pointed at the job the server started.
      expect(jobCtx.watchJobs).toHaveBeenCalled()
      expect(mockApi.post).not.toHaveBeenCalled()
      expect(jobCtx.startAnnotation).not.toHaveBeenCalled()

      finish(SORTED)
      expect(await screen.findByText('7 sorted, 1 needs you')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Teach Me/ })).toBeInTheDocument()
      // The table is reloaded so its Sorted column catches up.
      expect(mockApi.get.mock.calls.filter(([p]) => p === '/statements').length).toBeGreaterThan(1)
    })

    it('says a job waiting behind another is waiting', async () => {
      pollJob.mockImplementation((_id, onProgress) => {
        onProgress({ status: 'queued', processed: 0, total: 0 })
        return new Promise(() => {})
      })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
      expect(await screen.findByText('Waiting to sort')).toBeInTheDocument()
    })

    it('says plainly when no model is connected, with the way to Settings', async () => {
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
      expect(await screen.findByText(/No model is connected/)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Settings › AI & Model/ }).getAttribute('href')).toBe('#/settings?tab=ai')
    })

    it('says when the model stopped answering partway, with the way to Settings', async () => {
      pollJob.mockResolvedValue({ ...SORTED, ai_active: true, llm_error: 'connection refused' })
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
      expect(await screen.findByText(/model stopped answering \(connection refused\)/)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Settings › AI & Model/ }).getAttribute('href')).toBe('#/settings?tab=ai')
    })

    it('reports a sorting job that failed, and keeps the statement', async () => {
      pollJob.mockRejectedValue(new Error('Went stale without a worker'))
      const { container } = renderUpload()
      fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [pdf()] } })
      fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
      expect(await screen.findByText(/Sorting stopped/)).toBeInTheDocument()
      expect(screen.getByText(/8 payments added/)).toBeInTheDocument()
    })
  })
})
