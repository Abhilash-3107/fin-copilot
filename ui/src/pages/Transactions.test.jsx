import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from '../test/router.jsx'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), put: vi.fn() },
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
  { id: 't1', statement_id: 's2', txn_date: '2026-11-03', amount: 300, debit_credit: 'debit', raw_description: 'UPI/MYSTERY/501/x', counterparty_key: 'MYSTERY', payee: 'Mystery', payee_all: 'Mystery', note: 'lunch', channel: 'UPI', annotation_id: null },
  { id: 't2', statement_id: 's2', txn_date: '2026-11-04', amount: 250, debit_credit: 'credit', raw_description: 'UPI/REFUND/502/y', counterparty_key: 'REFUND', payee: 'Refund', payee_all: 'Refund', note: null, channel: 'UPI', annotation_id: null },
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

// The page loads its list once before the statements arrive and again scoped to the
// newest one, and every fresh result set clears the selection. Waits for that second
// load to land so a row picked afterwards is not cleared under the test.
async function rowsSettled() {
  await waitFor(() => expect(callsTo('/transactions?').at(-1)).toMatch(/statement_id=s2/))
  await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
  return screen.findAllByLabelText('Select transaction')
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
    if (/^\/transactions\/t\d/.test(path)) return Promise.resolve(TXNS.find(t => path.endsWith(t.id)))
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

  it('shows each payment by its payee with the note and channel beneath, not the bank line', async () => {
    renderPage()
    expect((await screen.findAllByText('Mystery')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('lunch · UPI').length).toBeGreaterThan(0)
    expect(screen.queryByText(/UPI\/MYSTERY/)).not.toBeInTheDocument()
  })

  it('updates the rows in place after a payee is renamed, without a reload or losing the open payment', async () => {
    const user = userEvent.setup()
    const sameKey = { ...TXNS[0], id: 't3', txn_date: '2026-11-02', raw_description: 'UPI/MYSTERY/503/z', note: null }
    mockApi.get.mockImplementation(path => {
      if (path === '/statements') return Promise.resolve(STATEMENTS)
      if (path.startsWith('/transactions/summary')) return Promise.resolve({ count: 3, money_out: 600, money_in: 250 })
      if (path.startsWith('/transactions/facets')) return Promise.resolve({ categories: [], sources: [] })
      if (/^\/transactions\/t\d/.test(path)) return Promise.resolve(TXNS.find(t => path.endsWith(t.id)))
      // Every payment to the payee, as the server names them after the rename.
      if (path.includes('payee_key=MYSTERY')) {
        return Promise.resolve([{ ...TXNS[0], payee: 'Rent', payee_all: 'Rent' }, { ...sameKey, payee: 'Rent', payee_all: 'Rent' }])
      }
      if (path.startsWith('/transactions')) return Promise.resolve([...TXNS, sameKey])
      return Promise.resolve([])
    })
    mockApi.put.mockResolvedValue({ ...TXNS[0], payee: 'Rent', payee_all: 'Rent', payee_name: 'Rent' })
    renderPage()
    await user.click((await screen.findAllByText('Mystery'))[0])
    expect(await screen.findByText('As printed on the statement')).toBeInTheDocument()
    expect(screen.getByText('UPI/MYSTERY/501/x')).toBeInTheDocument()

    const lists = () => callsTo('/transactions?').filter(p => p.includes('sort=')).length
    const before = lists()
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.clear(screen.getByRole('textbox', { name: 'Name' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Rent')
    await user.click(screen.getByRole('button', { name: 'Save name' }))

    expect(mockApi.put).toHaveBeenCalledWith('/transactions/t1/payee-name', { name: 'Rent' })
    // Both rows of the payee, one of them not the open payment, now read Rent.
    await waitFor(() => expect(screen.getAllByText('Rent').length).toBeGreaterThanOrEqual(3)) // two rows + the panel
    expect(screen.queryByText('Mystery')).not.toBeInTheDocument()
    // Other payees are untouched, the list was not reloaded, and the panel stays open.
    expect(screen.getAllByText('Refund').length).toBeGreaterThan(0)
    expect(lists()).toBe(before)
    expect(screen.getByText('As printed on the statement')).toBeInTheDocument()
  })

  it('updates only the one row after a single payment is renamed', async () => {
    const user = userEvent.setup()
    mockApi.put.mockResolvedValue({ ...TXNS[0], payee: 'Deposit', display_name: 'Deposit' })
    renderPage()
    await user.click((await screen.findAllByText('Mystery'))[0])
    await screen.findByText('As printed on the statement')
    await user.click(screen.getByRole('button', { name: 'Rename' }))
    await user.click(screen.getByRole('radio', { name: 'Just this payment' }))
    await user.clear(screen.getByRole('textbox', { name: 'Name' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Deposit')
    await user.click(screen.getByRole('button', { name: 'Save name' }))

    await waitFor(() => expect(screen.getAllByText('Deposit').length).toBeGreaterThan(0))
    expect(callsTo('/transactions?').some(p => p.includes('payee_key='))).toBe(false)
  })

  it('drills by payee key: the list and the summary both get it, and the chip is named', async () => {
    renderPage(['/transactions?payee_key=AMIT.LANDLORD%40OKSBI&payee_key=OTHER'])
    await screen.findByText(/2 transactions/)
    const list = new URLSearchParams(callsTo('/transactions?').at(-1).split('?')[1])
    const summary = new URLSearchParams(callsTo('/transactions/summary').at(-1).split('?')[1])
    expect(list.getAll('payee_key')).toEqual(['AMIT.LANDLORD@OKSBI', 'OTHER'])
    expect(summary.getAll('payee_key')).toEqual(['AMIT.LANDLORD@OKSBI', 'OTHER'])
    // A cold link has no name to show, so the chip reads what the server calls the payee.
    expect(await screen.findByRole('button', { name: /Payee: Mystery/ })).toBeInTheDocument()
  })

  it('has a search box wide enough to read its prompt', async () => {
    renderPage()
    await screen.findAllByText('Mystery') // let the page settle before the test ends
    const box = screen.getByRole('searchbox')
    expect(box).toHaveAttribute('placeholder', 'Search payee, note or amount…')
    expect(box.className).toMatch(/w-(64|72|80)\b/)
  })
})

describe('Transactions when the API fails', () => {
  const down = () => Promise.reject(new TypeError('Failed to fetch'))
  const routed = () => mockApi.get.getMockImplementation()

  it('says the list could not load, never "No transactions", and recovers on Retry', async () => {
    const base = routed()
    let failing = true
    mockApi.get.mockImplementation(path => (failing && path.startsWith('/transactions?') ? down() : base(path)))
    renderPage()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load your transactions.")
    expect(screen.queryByText('No transactions')).not.toBeInTheDocument()
    expect(screen.queryByText(/Upload a statement to get started/)).not.toBeInTheDocument()
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect((await screen.findAllByText('Mystery')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps the rows and offers Retry when the next page fails', async () => {
    const base = routed()
    const page = Array.from({ length: 500 }, (_, i) => ({ ...TXNS[0], id: `p${i}` }))
    let failMore = true
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/transactions?') && path.includes('after=')) return failMore ? down() : Promise.resolve([{ ...TXNS[1], id: 'last' }])
      if (path.startsWith('/transactions?')) return Promise.resolve(page)
      return base(path)
    })
    renderPage()
    fireEvent.click(await screen.findByText('Load more'))
    const alert = await screen.findByRole('alert') // role queries over 500 rows are slow, so the Retry lookup stays inside the alert
    expect(alert).toHaveTextContent("Couldn't load more transactions.")
    expect(screen.getAllByText('Mystery').length).toBeGreaterThan(100)
    failMore = false
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getAllByText('Refund').length).toBeGreaterThan(0)
  })

  it('says the filter options could not load, beside the filters, and Retry loads them', async () => {
    const base = routed()
    let failing = true
    mockApi.get.mockImplementation(path => (failing && path.startsWith('/transactions/facets') ? down() : base(path)))
    renderPage()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load the filter options.")
    expect((await screen.findAllByText('Mystery')).length).toBeGreaterThan(0)
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('says the categories could not load when you pick rows to set one', async () => {
    const base = routed()
    let failing = true
    mockApi.get.mockImplementation(path => (failing && path === '/categories' ? down() : base(path)))
    renderPage()
    fireEvent.click((await rowsSettled())[0])
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load the categories.")
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('does not open a payment as new when its details could not load, and Retry loads them', async () => {
    const user = userEvent.setup()
    const base = routed()
    let failing = true
    mockApi.get.mockImplementation(path => (failing && /^\/transactions\/t\d/.test(path) ? down() : base(path)))
    renderPage()
    await user.click((await screen.findAllByText('Mystery'))[0])
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load this payment's details.")
    // A failed payment never poses as one to annotate: no such header, no save button.
    expect(screen.getByRole('heading', { name: "Couldn't load this payment" })).toBeInTheDocument()
    expect(screen.queryByText('Annotate transaction')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Save annotation|Update annotation/ })).not.toBeInTheDocument()
    failing = false
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Save annotation' })).toBeEnabled()
    expect(screen.getByRole('heading', { name: 'Annotate transaction' })).toBeInTheDocument()
  })
})

describe('Transactions opened for one payment', () => {
  const OCT = { id: 't9', statement_id: 's1', txn_date: '2026-10-05', amount: 90, debit_credit: 'debit', raw_description: 'UPI/OLDSHOP/509/q', counterparty_key: 'OLDSHOP', payee: 'Old Shop', payee_all: 'Old Shop', note: null, channel: 'UPI', annotation_id: null }

  it('switches to the payment\'s month, clears the statement scope and opens its panel', async () => {
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => {
      if (path === '/transactions/t9') return Promise.resolve(OCT)
      if (path.startsWith('/transactions?') && path.includes('month=2026-10')) return Promise.resolve([OCT])
      return base(path)
    })
    renderPage(['/transactions?txn=t9'])
    expect(await screen.findByText('UPI/OLDSHOP/509/q')).toBeInTheDocument()
    expect(screen.getByText('As printed on the statement')).toBeInTheDocument()
    await waitFor(() => {
      const last = callsTo('/transactions?').at(-1)
      expect(last).toMatch(/month=2026-10/)
      expect(last).not.toMatch(/statement_id=/)
    })
    // The month in the list is the payment's, so its row is on the page.
    expect((await screen.findAllByText('Old Shop')).length).toBeGreaterThan(1)
  })

  it('says so when the payment is gone, and still shows the list', async () => {
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/transactions/missing'
        ? Promise.reject(Object.assign(new Error('Transaction not found'), { status: 404 }))
        : base(path)
    ))
    renderPage(['/transactions?txn=missing'])
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't find that payment.")
    expect((await screen.findAllByText('Mystery')).length).toBeGreaterThan(0)
  })
})

describe('Transactions opened for one payment that fails to load', () => {
  it('offers Retry for a server or network failure, and only a 404 says it may be gone', async () => {
    const OCT = { id: 't9', statement_id: 's1', txn_date: '2026-10-05', amount: 90, debit_credit: 'debit', raw_description: 'UPI/OLDSHOP/509/q', counterparty_key: 'OLDSHOP', payee: 'Old Shop', payee_all: 'Old Shop', note: null, channel: 'UPI', annotation_id: null }
    const base = mockApi.get.getMockImplementation()
    let failing = true
    mockApi.get.mockImplementation(path => {
      if (path === '/transactions/t9') return failing ? Promise.reject(new TypeError('Failed to fetch')) : Promise.resolve(OCT)
      return base(path)
    })
    renderPage(['/transactions?txn=t9'])
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't open this payment.")
    expect(alert).not.toHaveTextContent('removed')
    failing = false
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('UPI/OLDSHOP/509/q')).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't open this payment/)).not.toBeInTheDocument()
  })
})

describe('Transactions panel keys', () => {
  it('Escape inside a field only leaves the field; the next Escape closes the panel', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByText('Mystery'))[0])
    await screen.findByText('As printed on the statement')
    const tags = screen.getByPlaceholderText(/type and press Enter/)
    await user.click(tags)
    await user.type(tags, 'trip')
    await user.keyboard('{Escape}')
    expect(screen.getByText('As printed on the statement')).toBeInTheDocument()
    expect(tags).not.toHaveFocus()
    expect(tags).toHaveValue('trip')
    await user.keyboard('{Escape}')
    expect(screen.queryByText('As printed on the statement')).not.toBeInTheDocument()
  })
})
