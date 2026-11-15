import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
  runAnnotationJob: vi.fn(),
  pollAnnotationJob: vi.fn(),
  getActiveAnnotationJob: vi.fn().mockResolvedValue(null),
  ApiError: class ApiError extends Error {},
}))
vi.mock('../contexts/ToastContext.jsx', () => {
  const noop = () => {}
  return {
    ToastProvider: ({ children }) => children,
    useToast: () => noop,
  }
})

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import { StatementProvider } from '../contexts/StatementContext.jsx'
import { PeriodProvider } from '../contexts/PeriodContext.jsx'
import { AnnotationJobProvider } from '../contexts/AnnotationJobContext.jsx'
import Transactions from './Transactions.jsx'

const STATEMENTS = [
  { id: 's2', bank_name: 'kotak', statement_month: '2026-11', uploaded_at: '2026-11-15' },
  { id: 's1', bank_name: 'kotak', statement_month: '2026-10', uploaded_at: '2026-11-01' },
]

const TXNS = [
  { id: 't1', statement_id: 's2', txn_date: '2026-11-03', amount: 300, debit_credit: 'debit', raw_description: 'UPI/MYSTERY', annotation_id: null },
  { id: 't2', statement_id: 's2', txn_date: '2026-11-04', amount: 250, debit_credit: 'credit', raw_description: 'UPI/REFUND', annotation_id: null },
]

function renderPage(initialEntries = ['/transactions']) {
  render(
    <MemoryRouter initialEntries={initialEntries}>
      <PrivacyProvider>
        <StatementProvider>
          <PeriodProvider>
            <AnnotationJobProvider>
              <Transactions />
            </AnnotationJobProvider>
          </PeriodProvider>
        </StatementProvider>
      </PrivacyProvider>
    </MemoryRouter>
  )
}

const callsTo = prefix => mockApi.get.mock.calls.map(c => c[0]).filter(p => p.startsWith(prefix))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  mockApi.get.mockImplementation(path => {
    if (path === '/statements') return Promise.resolve(STATEMENTS)
    if (path.startsWith('/transactions/summary')) {
      return Promise.resolve({ count: 2, money_out: 300, money_in: 250 })
    }
    if (path.startsWith('/transactions/facets')) return Promise.resolve({ categories: ['Uncategorized'], sources: [] })
    if (path.startsWith('/transactions')) return Promise.resolve(TXNS)
    return Promise.resolve([])
  })
})

describe('Transactions', () => {
  it('summarises exactly the filtered rows in one line', async () => {
    renderPage()
    expect(await screen.findByText(
      (_, el) => el.tagName === 'SPAN' && el.textContent === '2 transactions · ₹300 out · ₹250 in'
    )).toBeInTheDocument()
  })

  it('asks for the summary with the same filters as the list', async () => {
    renderPage(['/transactions?category=Uncategorized&month=2026-11'])
    await screen.findByText(/2 transactions/)
    const list = new URLSearchParams(callsTo('/transactions?').at(-1).split('?')[1])
    const summary = new URLSearchParams(callsTo('/transactions/summary').at(-1).split('?')[1])
    expect(summary.get('category')).toBe('Uncategorized')
    for (const key of ['category', 'month', 'statement_id', 'q', 'source', 'merchant']) {
      expect(summary.get(key)).toBe(list.get(key))
    }
    // Pagination and ordering belong to the list only.
    expect(summary.has('limit')).toBe(false)
    expect(summary.has('sort')).toBe(false)
  })

  it('All time clears the statement scope so the list really shows everything', async () => {
    localStorage.setItem('fc_period_month', 'all')
    renderPage()
    const statementSelect = (await screen.findAllByRole('combobox'))[0]
    await waitFor(() => expect(statementSelect).toHaveValue(''))
    expect(statementSelect).toHaveDisplayValue('All statements')
    await waitFor(() => {
      const last = callsTo('/transactions?').at(-1)
      expect(last).not.toMatch(/statement_id=/)
      expect(last).not.toMatch(/month=/)
    })
  })

  it('choosing All time after a month clears the statement select too', async () => {
    renderPage()
    await screen.findByText(/2 transactions/)
    const [statementSelect, periodSelect] = screen.getAllByRole('combobox')
    await waitFor(() => expect(periodSelect).toHaveValue('2026-11'))
    expect(statementSelect).toHaveValue('s2')
    fireEvent.change(periodSelect, { target: { value: 'all' } })
    await waitFor(() => expect(statementSelect).toHaveValue(''))
    await waitFor(() => expect(callsTo('/transactions?').at(-1)).not.toMatch(/statement_id=|month=/))
  })
})
