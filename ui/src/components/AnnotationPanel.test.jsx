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
  counterparty_key: 'SWIGGY',
  payee: 'Swiggy',
  payee_all: 'Swiggy',
  payee_name: null,
  display_name: null,
  note: 'dinner',
  channel: 'UPI',
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
    <PrivacyProvider>
      <ToastProvider>
        <AnnotationPanel txn={TXN} onClose={() => {}} onSaved={() => {}} {...props} />
      </ToastProvider>
    </PrivacyProvider>
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

    await user.click(screen.getByRole('button', { name: 'Update annotation' }))

    await vi.waitFor(() =>
      expect(mockApi.patch).toHaveBeenCalledWith('/annotations/5', expect.objectContaining({ category: 'Food' }))
    )
    expect(mockApi.post).not.toHaveBeenCalledWith('/annotations', expect.anything())
  })

  it('has no merchant field: Rename is the only naming control, and a save leaves the machine merchant alone', async () => {
    const user = userEvent.setup()
    renderPanel({
      annotation: { id: 5, category: 'Food', subcategory: '', merchant: 'Swiggy', tags: ['x'], source: 'llm', confidence: 0.5 },
    })
    expect(screen.queryByPlaceholderText('e.g. Swiggy')).not.toBeInTheDocument()
    expect(screen.queryByText('Merchant')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Update annotation' }))

    await vi.waitFor(() => expect(mockApi.patch).toHaveBeenCalled())
    expect(mockApi.patch.mock.calls[0][1]).not.toHaveProperty('merchant')
    expect(mockApi.patch.mock.calls[0][1].tags).toEqual(['x'])
  })
})

describe('AnnotationPanel naming', () => {
  const RENAMED = { ...TXN, payee: 'Rent', payee_name: 'Rent' }

  it('leads with the payee and keeps the bank line under its own label', () => {
    renderPanel({ annotation: null })
    expect(screen.getByRole('heading', { name: 'Swiggy' })).toBeInTheDocument()
    expect(screen.getByText('As printed on the statement')).toBeInTheDocument()
    expect(screen.getByText('SWIGGY ORDER 8842')).toBeInTheDocument()
    expect(screen.getByText('dinner')).toBeInTheDocument()
  })

  it('renames every payment to the payee', async () => {
    const user = userEvent.setup()
    const onRenamed = vi.fn()
    mockApi.put = vi.fn().mockResolvedValue(RENAMED)
    renderPanel({ annotation: null, onRenamed })

    await user.click(screen.getByRole('button', { name: 'Rename' }))
    const input = screen.getByRole('textbox', { name: 'Name' })
    expect(input).toHaveValue('Swiggy')
    await user.clear(input)
    await user.type(input, 'Rent')
    expect(screen.getByRole('radio', { name: 'Every payment to Swiggy' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Save name' }))

    expect(mockApi.put).toHaveBeenCalledWith('/transactions/100/payee-name', { name: 'Rent' })
    expect(onRenamed).toHaveBeenCalledWith(RENAMED, 'payee')
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
  })

  it('renames just this payment', async () => {
    const user = userEvent.setup()
    mockApi.put = vi.fn().mockResolvedValue({ ...TXN, payee: 'Deposit', display_name: 'Deposit' })
    renderPanel({ annotation: null })

    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.click(screen.getByRole('radio', { name: 'Just this payment' }))
    const input = screen.getByRole('textbox', { name: 'Name' })
    await user.clear(input)
    await user.type(input, 'Deposit')
    await user.click(screen.getByRole('button', { name: 'Save name' }))

    expect(mockApi.put).toHaveBeenCalledWith('/transactions/100/name', { name: 'Deposit' })
  })

  it('offers only this payment when the line has no payee to rename', async () => {
    const user = userEvent.setup()
    renderPanel({ annotation: null, txn: { ...TXN, counterparty_key: null, payee_all: null, payee: 'ATM WITHDRAWAL' } })
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    expect(screen.queryByRole('radio', { name: /Every payment/ })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Just this payment' })).toBeChecked()
  })

  it('says the note is the user\'s own for money out and just a note for money in', () => {
    const { unmount } = renderPanel({ annotation: null })
    expect(screen.getByText('Your note')).toBeInTheDocument()
    unmount()
    renderPanel({ annotation: null, txn: { ...TXN, debit_credit: 'credit' } })
    expect(screen.queryByText('Your note')).not.toBeInTheDocument()
    expect(screen.getByText('Note')).toBeInTheDocument()
  })

  it('says when this payment keeps its own name under a payee rename, and clears just that name', async () => {
    const user = userEvent.setup()
    mockApi.put = vi.fn().mockResolvedValue(TXN)
    renderPanel({ annotation: null, txn: { ...TXN, payee: 'Deposit', display_name: 'Deposit' } })

    await user.click(screen.getByRole('button', { name: 'Rename' }))
    expect(screen.queryByText(/keeps its own name/)).not.toBeInTheDocument() // the payment scope is the default
    await user.click(screen.getByRole('radio', { name: 'Every payment to Swiggy' }))
    expect(screen.getByText('This payment keeps its own name, Deposit')).toBeInTheDocument()
    // The name field offers the payee's name, not this payment's own.
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Swiggy')

    await user.click(screen.getByRole('button', { name: 'Clear name' }))
    expect(mockApi.put).toHaveBeenCalledWith('/transactions/100/name', { name: null })
  })

  it('does not mention an own name that is not there', async () => {
    const user = userEvent.setup()
    renderPanel({ annotation: null })
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    expect(screen.queryByText(/keeps its own name/)).not.toBeInTheDocument()
  })

  it('puts focus back on Rename when the form closes', async () => {
    const user = userEvent.setup()
    mockApi.put = vi.fn().mockResolvedValue(TXN)
    renderPanel({ annotation: null })

    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.click(screen.getAllByRole('button', { name: 'Cancel' })[0]) // the form's, before the panel footer's
    expect(screen.getByRole('button', { name: 'Rename' })).toHaveFocus()

    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'x')
    await user.click(screen.getByRole('button', { name: 'Save name' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Rename' })).toHaveFocus())
  })

  it('clears a name so the next level down shows again', async () => {
    const user = userEvent.setup()
    mockApi.put = vi.fn().mockResolvedValue(TXN)
    renderPanel({ annotation: null, txn: RENAMED })

    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.click(screen.getByRole('button', { name: 'Clear name' }))

    expect(mockApi.put).toHaveBeenCalledWith('/transactions/100/payee-name', { name: null })
  })

  it('refuses an empty name instead of clearing by accident', async () => {
    const user = userEvent.setup()
    mockApi.put = vi.fn()
    renderPanel({ annotation: null })
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.clear(screen.getByRole('textbox', { name: 'Name' }))
    expect(screen.getByRole('button', { name: 'Save name' })).toBeDisabled()
  })

  it('says so when the rename fails', async () => {
    const user = userEvent.setup()
    mockApi.put = vi.fn().mockRejectedValue(new Error('boom'))
    renderPanel({ annotation: null })
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'x')
    await user.click(screen.getByRole('button', { name: 'Save name' }))
    expect(toastSpy).toHaveBeenCalledWith('Rename failed: boom', 'error')
    expect(screen.getByRole('textbox', { name: 'Name' })).toBeInTheDocument()
  })
})
