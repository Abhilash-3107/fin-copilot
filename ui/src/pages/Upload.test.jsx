import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { HashRouter } from 'react-router-dom'

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

const { demoCtx } = vi.hoisted(() => ({ demoCtx: { sample: false, removable: false, mixed: false, hosted: false, refresh: vi.fn() } }))
vi.mock('../contexts/DemoDataContext.jsx', () => ({
  useDemoData: () => demoCtx,
}))

const stmt = {
  id: 's1', bank_name: 'kotak', statement_month: '2026-07', balance_check: null,
  txn_count: 10, annotated_count: 2, embedded_count: 10, uploaded_at: '2026-07-01T10:00:00',
}

import Upload from './Upload.jsx'

const renderUpload = () => render(<HashRouter><Upload /></HashRouter>)

const pdf = () => new File(['%PDF-1.4'], 'statement.pdf', { type: 'application/pdf' })

describe('Upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    jobCtx.job = null
    jobCtx.startAnnotation = vi.fn()
    Object.assign(demoCtx, { sample: false, removable: false, mixed: false, hosted: false })
    mockApi.get.mockResolvedValue([])
    mockApi.upload.mockResolvedValue({ bank_name: 'kotak', statement_month: '2026-07', warnings: [] })
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
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /categorize/i }))
    await waitFor(() => expect(jobCtx.startAnnotation).toHaveBeenCalledWith({ statement_id: 's1' }))
    expect(await screen.findByText('Figured out by AI: 2')).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations/auto-annotate', expect.anything())
  })

  it('every Categorize waits while another run is tracked', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.job = { status: 'running', kind: 'reembed' }
    renderUpload()
    expect(await screen.findByRole('button', { name: /categorize/i })).toBeDisabled()
  })

  it('shows the provider error in the results panel', async () => {
    mockApi.get.mockResolvedValue([stmt])
    jobCtx.startAnnotation.mockResolvedValue({
      total_processed: 8, rule_matched: 5, llm_annotated: 0, llm_failed: 3,
      ai_active: true, llm_error: 'provider refused the request (HTTP 404)',
    })
    renderUpload()
    fireEvent.click(await screen.findByRole('button', { name: /categorize/i }))
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
    fireEvent.click(await screen.findByRole('button', { name: /categorize/i }))
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
    fireEvent.click(await screen.findByRole('button', { name: /categorize/i }))
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
})
