import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))
const toast = vi.fn()
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toast,
}))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import UserRules from './UserRules.jsx'

const TREE = [
  { id: 'food', name: 'Food & Dining', parent_id: null },
  { id: 'groc', name: 'Groceries', parent_id: 'food' },
]
const RULE = {
  id: 'r1', pattern: 'zepto', category: 'Food & Dining', subcategory: 'Groceries', merchant: null, enabled: true,
}
const PREVIEW = {
  matches: 3, relabel: 1, unlabeled: 1, unchanged: 0, protected: 1,
  sample: [{ id: 't1', txn_date: '2026-10-01', amount: 420, debit_credit: 'debit', raw_description: 'UPI/ZEPTO/1', category: 'Shopping' }],
}

function renderRules() {
  return render(<PrivacyProvider><UserRules /></PrivacyProvider>)
}

async function fillDraft() {
  fireEvent.change(screen.getByLabelText(/When the transaction mentions/), { target: { value: 'zepto' } })
  // CategoryPicker loads the taxonomy asynchronously; pick once it's there.
  await screen.findByRole('option', { name: 'Food & Dining' })
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'Food & Dining' } })
}

describe('UserRules', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockImplementation(path => Promise.resolve(path === '/categories' ? TREE : []))
    mockApi.post.mockImplementation(path => {
      if (path === '/rules/preview') return Promise.resolve(PREVIEW)
      if (path === '/rules') return Promise.resolve(RULE)
      return Promise.resolve({ relabeled: 1, labeled: 1, protected: 1, unchanged: 0 })
    })
  })

  it('previews what a rule would change before saving', async () => {
    renderRules()
    await fillDraft()
    await waitFor(() => expect(screen.getByText(/Matches 3 transactions/)).toBeInTheDocument())
    expect(screen.getByText(/1 you labeled yourself \(kept\)/)).toBeInTheDocument()
    expect(screen.getByText('UPI/ZEPTO/1')).toBeInTheDocument()
    expect(mockApi.post).not.toHaveBeenCalledWith('/rules', expect.anything())
  })

  it('adds the rule, and applies it to existing matches only when asked', async () => {
    renderRules()
    await fillDraft()
    await waitFor(() => expect(screen.getByText(/Also categorize the 2 existing matches now/)).toBeInTheDocument())
    fireEvent.click(screen.getByText('Add rule'))
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/rules', expect.objectContaining({ pattern: 'zepto' })))
    expect(mockApi.post).not.toHaveBeenCalledWith('/rules/r1/apply')
    expect(await screen.findByText('"zepto"')).toBeInTheDocument()

    await fillDraft()
    await screen.findByRole('checkbox')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByText('Add rule'))
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/rules/r1/apply'))
  })

  it('pauses and removes a rule', async () => {
    mockApi.get.mockImplementation(path => Promise.resolve(path === '/categories' ? TREE : [RULE]))
    mockApi.patch.mockResolvedValue({ ...RULE, enabled: false })
    mockApi.delete.mockResolvedValue(null)
    renderRules()
    fireEvent.click(await screen.findByLabelText('Pause rule "zepto"'))
    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith('/rules/r1', { enabled: false }))
    fireEvent.click(screen.getByLabelText('Remove rule "zepto"'))
    await waitFor(() => expect(screen.queryByText('"zepto"')).not.toBeInTheDocument())
  })
})
