import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), upload: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, downloadFile: vi.fn(), ApiError: class extends Error {} }))
const toast = vi.fn()
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toast,
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
    await waitFor(() => expect(toast).toHaveBeenCalledWith(
      expect.stringContaining('pre-restore-20261101-050000.db'), 'success', 8000))
  })

  it('does nothing when cancelled', () => {
    render(<DataSettings />)
    pick()
    fireEvent.click(screen.getByText('Cancel'))
    expect(screen.queryByText('Restore from this backup?')).not.toBeInTheDocument()
    expect(mockApi.upload).not.toHaveBeenCalled()
  })
})
