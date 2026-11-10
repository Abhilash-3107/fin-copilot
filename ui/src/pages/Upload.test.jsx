import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
const noopToast = () => {}
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => noopToast,
}))
const { jobCtx } = vi.hoisted(() => ({ jobCtx: { job: null, startAnnotation: null } }))
vi.mock('../contexts/AnnotationJobContext.jsx', () => ({
  useAnnotationJob: () => jobCtx,
}))

const stmt = {
  id: 's1', bank_name: 'kotak', statement_month: '2026-07', balance_check: null,
  txn_count: 10, annotated_count: 2, embedded_count: 10, uploaded_at: '2026-07-01T10:00:00',
}

import Upload from './Upload.jsx'

const pdf = () => new File(['%PDF-1.4'], 'statement.pdf', { type: 'application/pdf' })

describe('Upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    jobCtx.job = null
    jobCtx.startAnnotation = vi.fn()
    mockApi.get.mockResolvedValue([])
    mockApi.upload.mockResolvedValue({ bank_name: 'kotak', statement_month: '2026-07', warnings: [] })
  })

  it('clears the file input after a pick, so the same file can be picked again', async () => {
    const { container } = render(<Upload />)
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

    fireEvent.click(screen.getByRole('button', { name: /upload/i }))
    await waitFor(() => expect(mockApi.upload).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.queryByText('statement.pdf')).not.toBeInTheDocument())

    fireEvent.change(input, { target: { files: [pdf()] } })
    expect(screen.getByText('statement.pdf')).toBeInTheDocument()
  })

  it("Categorize runs through the job flow and shows that run's result", async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, rag_direct_annotated: 1, llm_annotated: 2,
      llm_failed: 0, low_confidence: 0, already_annotated: 2, ai_active: true, llm_error: null,
    })
    render(<Upload />)
    fireEvent.click(await screen.findByRole('button', { name: /categorize/i }))
    await waitFor(() => expect(jobCtx.startAnnotation).toHaveBeenCalledWith({ statement_id: 's1' }))
    expect(await screen.findByText('Figured out by AI: 2')).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/auto-annotate', expect.anything())
  })

  it('every Categorize waits while another run is tracked', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.job = { status: 'running', kind: 'reembed' }
    render(<Upload />)
    expect(await screen.findByRole('button', { name: /categorize/i })).toBeDisabled()
  })

  it('shows the provider error in the results panel', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: true, llm_error: 'provider refused the request (HTTP 404)',
    })
    render(<Upload />)
    fireEvent.click(await screen.findByRole('button', { name: /categorize/i }))
    expect(await screen.findByText(/isn.t available \(provider refused the request \(HTTP 404\)\)/))
      .toBeInTheDocument()
  })
})
