import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const { mockApi, toastSpy } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  toastSpy: vi.fn(),
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
  ApiError: class ApiError extends Error {},
}))
// Spy toast: lets us assert messages without the real provider's auto-dismiss
// setTimeout firing after the test (which trips React's act() warning).
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toastSpy,
}))

import { ToastProvider } from '../contexts/ToastContext.jsx'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import AnnotationPanel from './AnnotationPanel.jsx'

const CATEGORIES = [
  { id: 1, name: 'Food', parent_id: null },
  { id: 2, name: 'Transport', parent_id: null },
]

const TXN = {
  id: 100,
  txn_date: '2026-01-01',
  amount: 250,
  debit_credit: 'debit',
  raw_description: 'SWIGGY ORDER 8842',
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockImplementation(path => {
    if (path === '/categories') return Promise.resolve(CATEGORIES)
    if (path.startsWith('/groups/for-transaction')) return Promise.resolve([])
    return Promise.resolve(null)
  })
  mockApi.post.mockResolvedValue({ id: 999 })
  mockApi.patch.mockResolvedValue({})
})

function renderPanel(props) {
  return render(
    <ToastProvider>
      <PrivacyProvider>
        <AnnotationPanel txn={TXN} onClose={() => {}} onSaved={() => {}} {...props} />
      </PrivacyProvider>
    </ToastProvider>
  )
}

describe('AnnotationPanel', () => {
  it('refuses to save without a category', async () => {
    const user = userEvent.setup()
    renderPanel({ annotation: null })

    await user.click(screen.getByRole('button', { name: 'Save annotation' }))

    expect(toastSpy).toHaveBeenCalledWith('Category is required', 'error')
    expect(mockApi.post).not.toHaveBeenCalled()
    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('creates a manual annotation for an unannotated transaction', async () => {
    const user = userEvent.setup()
    renderPanel({ annotation: null })

    // Wait for the category options to load, then pick one.
    await screen.findByRole('option', { name: 'Food' })
    const [categorySelect] = screen.getAllByRole('combobox')
    await user.selectOptions(categorySelect, 'Food')

    await user.click(screen.getByRole('button', { name: 'Save annotation' }))

    await vi.waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith(
        '/annotations',
        expect.objectContaining({
          category: 'Food',
          transaction_id: 100,
          source: 'manual',
          confidence: 1.0,
        })
      )
    )
    expect(mockApi.patch).not.toHaveBeenCalled()
  })

  it('patches an existing annotation instead of creating one', async () => {
    const user = userEvent.setup()
    renderPanel({
      annotation: { id: 5, category: 'Food', subcategory: '', merchant: 'Swiggy', tags: [], source: 'llm', confidence: 0.5 },
    })

    const merchant = screen.getByPlaceholderText('e.g. Swiggy')
    await user.clear(merchant)
    await user.type(merchant, 'Zomato')

    await user.click(screen.getByRole('button', { name: 'Update annotation' }))

    await vi.waitFor(() =>
      expect(mockApi.patch).toHaveBeenCalledWith(
        '/annotations/5',
        expect.objectContaining({ category: 'Food', merchant: 'Zomato' })
      )
    )
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations', expect.anything())
  })
})
