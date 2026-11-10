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

import Upload from './Upload.jsx'

const pdf = () => new File(['%PDF-1.4'], 'statement.pdf', { type: 'application/pdf' })

describe('Upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
})
