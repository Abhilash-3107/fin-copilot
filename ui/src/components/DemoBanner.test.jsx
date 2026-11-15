import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
const toast = vi.fn()
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toast,
}))

import DemoBanner from './DemoBanner.jsx'
import { DemoDataProvider } from '../contexts/DemoDataContext.jsx'

function serve({ demoMode = false, loaded = false, mixed = false } = {}) {
  mockApi.get.mockImplementation(path => {
    if (path === '/health') return Promise.resolve({ demo_mode: demoMode })
    if (path === '/data/demo') return Promise.resolve({ loaded, mixed })
    return Promise.reject(new Error(`unexpected ${path}`))
  })
}

function renderBanner() {
  return render(<DemoDataProvider><DemoBanner /></DemoDataProvider>)
}

describe('DemoBanner', () => {
  beforeEach(() => vi.clearAllMocks())

  it('says nothing on a ledger without sample data', async () => {
    serve()
    const { container } = renderBanner()
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/data/demo'))
    expect(container).toBeEmptyDOMElement()
  })

  it('says so while sample data is present, and offers to remove it', async () => {
    serve({ loaded: true })
    renderBanner()
    expect(await screen.findByText("You're looking at sample data.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove sample data' })).toBeInTheDocument()
  })

  it('says the data is mixed when real statements sit next to it', async () => {
    serve({ loaded: true, mixed: true })
    renderBanner()
    expect(await screen.findByText(/mixed in with your statements/)).toBeInTheDocument()
  })

  it('keeps the hosted demo message, with no remove action', async () => {
    serve({ demoMode: true, loaded: true })
    renderBanner()
    expect(await screen.findByText(/this demo can't accept uploads/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove sample data' })).not.toBeInTheDocument()
  })

  it('stays off when the status cannot be read', async () => {
    mockApi.get.mockRejectedValue(new Error('offline'))
    const { container } = renderBanner()
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  describe('removing', () => {
    const reload = vi.fn()
    const realLocation = window.location

    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: { ...realLocation, reload, hash: '#/transactions' },
      })
    })
    afterEach(() => {
      vi.useRealTimers()
      Object.defineProperty(window, 'location', { configurable: true, value: realLocation })
    })

    it('asks first and does nothing when cancelled', async () => {
      serve({ loaded: true })
      renderBanner()
      fireEvent.click(await screen.findByRole('button', { name: 'Remove sample data' }))
      expect(screen.getByText('Remove the sample data?')).toBeInTheDocument()
      fireEvent.click(screen.getByText('Cancel'))
      expect(mockApi.delete).not.toHaveBeenCalled()
    })

    it('calls the route on confirm and returns to the first-run dashboard', async () => {
      serve({ loaded: true })
      mockApi.delete.mockResolvedValue({ transactions: 195 })
      renderBanner()
      fireEvent.click(await screen.findByRole('button', { name: 'Remove sample data' }))
      // Two buttons now carry that label: the strip's, and the dialog's confirm.
      const [, confirm] = screen.getAllByRole('button', { name: 'Remove sample data' })
      fireEvent.click(confirm)
      await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/data/demo'))
      await waitFor(() => expect(toast).toHaveBeenCalledWith('Sample data removed.', 'info'))
      vi.advanceTimersByTime(700)
      expect(window.location.hash).toBe('#/dashboard')
      expect(reload).toHaveBeenCalled()
    })

    it('reports a failure and stays put', async () => {
      serve({ loaded: true })
      mockApi.delete.mockRejectedValue(new Error('A categorization is running'))
      renderBanner()
      fireEvent.click(await screen.findByRole('button', { name: 'Remove sample data' }))
      fireEvent.click(screen.getAllByRole('button', { name: 'Remove sample data' })[1])
      await waitFor(() => expect(toast).toHaveBeenCalledWith(
        expect.stringContaining('A categorization is running'), 'error', 5000))
      expect(reload).not.toHaveBeenCalled()
    })
  })
})
