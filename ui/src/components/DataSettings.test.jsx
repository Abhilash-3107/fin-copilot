import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, downloadFile: vi.fn(), ApiError: class extends Error {} }))
const toast = vi.fn()
const reloadWithToast = vi.fn()
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toast,
  reloadWithToast: (...args) => reloadWithToast(...args),
}))

const { demoCtx } = vi.hoisted(() => ({ demoCtx: { sample: false, mixed: false, hosted: false, refresh: () => {} } }))
vi.mock('../contexts/DemoDataContext.jsx', () => ({
  useDemoData: () => demoCtx,
}))

import DataSettings from './DataSettings.jsx'

function pick(name = 'finance-copilot-backup.db') {
  const file = new File(['SQLite format 3'], name, { type: 'application/x-sqlite3' })
  fireEvent.change(screen.getByLabelText('Backup file to restore'), { target: { files: [file] } })
  return file
}

describe('DataSettings restore', () => {
  beforeEach(() => vi.clearAllMocks())

  it('asks before replacing anything, naming the file', () => {
    render(<DataSettings />)
    pick('march.db')
    expect(screen.getByText('Restore from this backup?')).toBeInTheDocument()
    expect(screen.getByText(/contents of march\.db/)).toBeInTheDocument()
    expect(mockApi.upload).not.toHaveBeenCalled()
  })

  it('uploads on confirm and reports where the old data went', async () => {
    mockApi.upload.mockResolvedValue({ transactions: 668, safety_copy: 'pre-restore-20261101-050000.db' })
    render(<DataSettings />)
    const file = pick()
    fireEvent.click(screen.getByText('Replace and restore'))
    await waitFor(() => expect(mockApi.upload).toHaveBeenCalledWith('/data/restore', expect.any(FormData)))
    expect(mockApi.upload.mock.calls[0][1].get('file')).toBe(file)
    // The app reloads after a restore; the message is carried across it.
    await waitFor(() => expect(reloadWithToast).toHaveBeenCalledWith(
      expect.stringContaining('pre-restore-20261101-050000.db'), 'success'))
  })

  it('does nothing when cancelled', () => {
    render(<DataSettings />)
    pick()
    fireEvent.click(screen.getByText('Cancel'))
    expect(screen.queryByText('Restore from this backup?')).not.toBeInTheDocument()
    expect(mockApi.upload).not.toHaveBeenCalled()
  })
})

describe('DataSettings sample data', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.assign(demoCtx, { sample: false, mixed: false })
  })

  it('offers to remove sample data while the ledger has it', () => {
    demoCtx.sample = true
    render(<DataSettings />)
    expect(screen.getByText('Sample data')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove sample data' })).toBeInTheDocument()
  })

  it('says so when real statements sit next to the sample data', () => {
    demoCtx.sample = true
    demoCtx.mixed = true
    render(<DataSettings />)
    expect(screen.getByText(/next to your own statements/)).toBeInTheDocument()
  })

  it('has no sample-data section on a ledger without it', () => {
    render(<DataSettings />)
    expect(screen.queryByText('Sample data')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove sample data' })).not.toBeInTheDocument()
  })
})
