import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { Routes, Route, useNavigate } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from '../test/router.jsx'

const { mockApi, toastSpy } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), put: vi.fn(), post: vi.fn(), patch: vi.fn() },
  toastSpy: vi.fn(),
}))
vi.mock('../lib/api.js', () => ({
  api: mockApi,
  runAnnotationJob: vi.fn(),
  pollAnnotationJob: vi.fn(),
  getActiveAnnotationJob: vi.fn().mockResolvedValue(null),
  ApiError: class ApiError extends Error {},
}))
vi.mock('../contexts/ToastContext.jsx', () => ({
  ToastProvider: ({ children }) => children,
  useToast: () => toastSpy,
}))

// The real picker caches the taxonomy for the whole module, so a test cannot choose what it holds.
vi.mock('../components/CategoryPicker.jsx', () => ({
  default: ({ category, onChange }) => (
    <select aria-label="Category" value={category} onChange={e => onChange({ category: e.target.value, subcategory: '' })}>
      <option value="">Select a category</option>
      <option value="Self Transfers">Self Transfers</option>
      <option value="Food & Dining">Food & Dining</option>
    </select>
  ),
}))

// A full page is 500 rows, and drawing all of them is what made the "next page
// fails" test slow. The tables draw at most CAP rows; the page still holds, counts
// and pages through every one it was given.
const { CAP } = vi.hoisted(() => ({ CAP: 25 }))
vi.mock('../components/TransactionTable.jsx', async () => {
  const { default: Real } = await vi.importActual('../components/TransactionTable.jsx')
  return { default: props => <Real {...props} transactions={props.transactions.slice(0, CAP)} /> }
})
vi.mock('../components/TransactionCards.jsx', async () => {
  const { default: Real } = await vi.importActual('../components/TransactionCards.jsx')
  return { default: props => <Real {...props} transactions={props.transactions.slice(0, CAP)} /> }
})

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import { LedgerProvider } from '../contexts/LedgerContext.jsx'
import { PeriodProvider } from '../contexts/PeriodContext.jsx'
import { AnnotationJobProvider } from '../contexts/AnnotationJobContext.jsx'
import Transactions from './Transactions.jsx'

const KOTAK = { id: 'acc_k', account_label: 'Kotak savings', statement_count: 2 }
const SBI = { id: 'acc_s', account_label: 'SBI savings', statement_count: 1 }
// One account: no filter. Tests that need a choice serve TWO_ACCOUNTS.
const ONE_ACCOUNT = [KOTAK]
const TWO_ACCOUNTS = [KOTAK, SBI]

const TXNS = [
  { id: 't1', statement_id: 's2', account_id: 'acc_k', account_label: 'Kotak savings', running_balance: 1700, txn_date: '2026-11-03', amount: 300, debit_credit: 'debit', raw_description: 'UPI/MYSTERY/501/x', counterparty_key: 'MYSTERY', payee: 'Mystery', payee_all: 'Mystery', note: 'lunch', channel: 'UPI', annotation_id: null },
  { id: 't2', statement_id: 's2', account_id: 'acc_s', account_label: 'SBI savings', running_balance: 5250, txn_date: '2026-11-04', amount: 250, debit_credit: 'credit', raw_description: 'UPI/REFUND/502/y', counterparty_key: 'REFUND', payee: 'Refund', payee_all: 'Refund', note: null, channel: 'UPI', annotation_id: null },
]

function renderPage(initialEntries = ['/transactions']) {
  render(
    <MemoryRouter initialEntries={initialEntries}>
      <PrivacyProvider>
        <LedgerProvider>
          <PeriodProvider>
            <AnnotationJobProvider>
              <Transactions />
            </AnnotationJobProvider>
          </PeriodProvider>
        </LedgerProvider>
      </PrivacyProvider>
    </MemoryRouter>
  )
}

// The page asks for its list once the accounts and the period are settled, and every
// fresh result set clears the selection. Waits for that load to land so a row picked
// afterwards is not cleared under the test.
async function rowsSettled() {
  await waitFor(() => expect(callsTo('/transactions?')).toHaveLength(1))
  await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
  return screen.findAllByLabelText('Select payment')
}

const callsTo = prefix => mockApi.get.mock.calls.map(c => c[0]).filter(p => p.startsWith(prefix))

let accountsServed

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  accountsServed = ONE_ACCOUNT
  mockApi.get.mockImplementation(path => {
    if (path === '/accounts') return Promise.resolve(accountsServed)
    if (path.startsWith('/transactions/summary')) {
      return Promise.resolve({ count: 2, money_out: 300, money_in: 250 })
    }
    if (path === '/transactions/months') return Promise.resolve({ months: ['2026-10', '2026-11'] })
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
      (_, el) => el.tagName === 'SPAN' && el.textContent === '2 payments · ₹300 out · ₹250 in'
    )).toBeInTheDocument()
  })

  it('asks for the summary with the same filters as the list', async () => {
    renderPage(['/transactions?category=Uncategorized&month=2026-11'])
    await screen.findByText(/2 payments/)
    const list = new URLSearchParams(callsTo('/transactions?').at(-1).split('?')[1])
    const summary = new URLSearchParams(callsTo('/transactions/summary').at(-1).split('?')[1])
    expect(summary.get('category')).toBe('Uncategorized')
    for (const key of ['category', 'month', 'account_id', 'q', 'source', 'merchant']) {
      expect(summary.get(key)).toBe(list.get(key))
    }
    // Pagination and ordering belong to the list only.
    expect(summary.has('limit')).toBe(false)
    expect(summary.has('sort')).toBe(false)
  })

  it('has no account filter and no statement picker with a single account', async () => {
    renderPage()
    await screen.findAllByLabelText('Select payment')
    expect(screen.queryByRole('combobox', { name: 'Account' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('combobox')).toHaveLength(1) // the period
    expect(screen.queryByRole('button', { name: /Sort again/ })).not.toBeInTheDocument()
  })

  it('offers every account and All accounts when there are two, named as the server words them', async () => {
    accountsServed = TWO_ACCOUNTS
    renderPage()
    const filter = await screen.findByRole('combobox', { name: 'Account' })
    expect([...filter.options].map(o => o.textContent)).toEqual(['All accounts', 'Kotak savings', 'SBI savings'])
    expect(filter).toHaveValue('')
  })

  it('choosing an account asks again for that account, with the summary and the options alike', async () => {
    accountsServed = TWO_ACCOUNTS
    renderPage()
    await screen.findAllByLabelText('Select payment')
    fireEvent.change(screen.getByRole('combobox', { name: 'Account' }), { target: { value: 'acc_s' } })
    await waitFor(() => expect(callsTo('/transactions?').at(-1)).toMatch(/account_id=acc_s/))
    await waitFor(() => expect(callsTo('/transactions/summary').at(-1)).toMatch(/account_id=acc_s/))
    await waitFor(() => expect(callsTo('/transactions/facets').at(-1)).toMatch(/account_id=acc_s/))
    expect(localStorage.getItem('fc_account_id')).toBe('acc_s')
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
  })

  it('remembers the account across a visit, and asks once with it', async () => {
    accountsServed = TWO_ACCOUNTS
    localStorage.setItem('fc_account_id', 'acc_k')
    renderPage()
    await screen.findAllByLabelText('Select payment')
    expect(callsTo('/transactions?')).toHaveLength(1)
    expect(callsTo('/transactions?')[0]).toMatch(/account_id=acc_k/)
    expect(screen.getByRole('combobox', { name: 'Account' })).toHaveValue('acc_k')
  })

  it('forgets a remembered account that is gone, and shows every account', async () => {
    accountsServed = TWO_ACCOUNTS
    localStorage.setItem('fc_account_id', 'acc_gone')
    renderPage()
    await screen.findAllByLabelText('Select payment')
    expect(callsTo('/transactions?')).toHaveLength(1)
    expect(callsTo('/transactions?')[0]).not.toMatch(/account_id=/)
  })

  it('ignores a remembered account when only one account is left', async () => {
    localStorage.setItem('fc_account_id', 'acc_k')
    renderPage()
    await screen.findAllByLabelText('Select payment')
    expect(callsTo('/transactions?')[0]).not.toMatch(/account_id=/)
  })

  it('names the account on each row when several are shown, and hides the balance column', async () => {
    accountsServed = TWO_ACCOUNTS
    renderPage()
    await screen.findAllByLabelText('Select payment')
    const headers = screen.getAllByRole('columnheader').map(h => h.textContent)
    expect(headers).toContain('Account')
    expect(headers).not.toContain('Balance')
    expect(screen.getAllByText('SBI savings').length).toBeGreaterThan(1) // the option and a row
  })

  it('shows the running balance, and no account column, for one selected account', async () => {
    accountsServed = TWO_ACCOUNTS
    localStorage.setItem('fc_account_id', 'acc_k')
    renderPage()
    await screen.findAllByLabelText('Select payment')
    const headers = screen.getAllByRole('columnheader').map(h => h.textContent)
    expect(headers).toContain('Balance')
    expect(headers).not.toContain('Account')
    expect(screen.getByText('₹1,700.00')).toBeInTheDocument()
  })

  it('shows the running balance when the ledger holds a single account', async () => {
    renderPage()
    await screen.findAllByLabelText('Select payment')
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toContain('Balance')
  })

  it('All time drops the month and keeps the account', async () => {
    accountsServed = TWO_ACCOUNTS
    localStorage.setItem('fc_period_month', 'all')
    localStorage.setItem('fc_account_id', 'acc_s')
    renderPage()
    await screen.findAllByLabelText('Select payment')
    const last = callsTo('/transactions?').at(-1)
    expect(last).toMatch(/account_id=acc_s/)
    expect(last).not.toMatch(/month=/)
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
      if (path === '/accounts') return Promise.resolve(accountsServed)
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

  it('writes a note on a payment and the row shows it at once, without a reload', async () => {
    const user = userEvent.setup()
    mockApi.put.mockResolvedValue({ ...TXNS[0], note: 'team lunch', note_is_yours: true })
    renderPage()
    await user.click((await screen.findAllByText('Mystery'))[0])
    await screen.findByText('As printed on the statement')

    const lists = () => callsTo('/transactions?').filter(p => p.includes('sort=')).length
    const before = lists()
    await user.click(screen.getByRole('button', { name: 'Edit note' }))
    const input = screen.getByRole('textbox', { name: 'Your note' })
    expect(input).toHaveValue('lunch')
    await user.clear(input)
    await user.type(input, 'team lunch{Enter}')

    expect(mockApi.put).toHaveBeenCalledWith('/transactions/t1/note', { note: 'team lunch' })
    await waitFor(() => expect(screen.getAllByText('team lunch · UPI').length).toBeGreaterThan(0)) // the row
    expect(screen.queryByText('lunch · UPI')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit note' })).toBeInTheDocument()
    expect(lists()).toBe(before)
    expect(screen.getByText('As printed on the statement')).toBeInTheDocument()
  })

  it('drills by payee key: the list and the summary both get it, and the chip is named', async () => {
    renderPage(['/transactions?payee_key=AMIT.LANDLORD%40OKSBI&payee_key=OTHER'])
    await screen.findByText(/2 payments/)
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

  it('says the list could not load, never "No payments", and recovers on Retry', async () => {
    const base = routed()
    let failing = true
    mockApi.get.mockImplementation(path => (failing && path.startsWith('/transactions?') ? down() : base(path)))
    renderPage()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't load your payments.")
    expect(screen.queryByText('No payments')).not.toBeInTheDocument()
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
    const alert = await screen.findByRole('alert') 
    expect(alert).toHaveTextContent("Couldn't load more payments.")
    expect(screen.getAllByText('Mystery').length).toBeGreaterThanOrEqual(CAP)
    failMore = false
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    // The short last page arrived (the second ask for it), so there is no more to load.
    await waitFor(() => expect(screen.queryByText('Load more')).not.toBeInTheDocument())
    expect(callsTo('/transactions?').filter(p => p.includes('after='))).toHaveLength(2)
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
    // A failed payment never poses as one to sort: no such header, no save button.
    expect(screen.getByRole('heading', { name: "Couldn't load this payment" })).toBeInTheDocument()
    expect(screen.queryByText('Sort this payment')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^(Save|Update)$/ })).not.toBeInTheDocument()
    failing = false
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(screen.getByRole('heading', { name: 'Sort this payment' })).toBeInTheDocument()
  })
})

describe('Transactions opened for one payment', () => {
  const OCT = { id: 't9', statement_id: 's1', txn_date: '2026-10-05', amount: 90, debit_credit: 'debit', raw_description: 'UPI/OLDSHOP/509/q', counterparty_key: 'OLDSHOP', payee: 'Old Shop', payee_all: 'Old Shop', note: null, channel: 'UPI', annotation_id: null }

  it('switches to the payment\'s month and opens its panel', async () => {
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
    })
    // The month in the list is the payment's, so its row is on the page.
    expect((await screen.findAllByText('Old Shop')).length).toBeGreaterThan(1)
  })

  it('says so when the payment is gone, and still shows the list', async () => {
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (
      path === '/transactions/missing'
        ? Promise.reject(Object.assign(new Error('Payment not found'), { status: 404 }))
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

describe('Transactions opened for one payment while one account is selected', () => {
  const OCT_SBI = { id: 't9', statement_id: 's9', account_id: 'acc_s', account_label: 'SBI savings', txn_date: '2026-10-05', amount: 90, debit_credit: 'debit', raw_description: 'UPI/OLDSHOP/509/q', counterparty_key: 'OLDSHOP', payee: 'Old Shop', payee_all: 'Old Shop', note: null, channel: 'UPI', annotation_id: null }

  beforeEach(() => {
    accountsServed = TWO_ACCOUNTS
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => {
      if (path === '/transactions/t9') return Promise.resolve(OCT_SBI)
      if (path.startsWith('/transactions?') && path.includes('month=2026-10')) return Promise.resolve([OCT_SBI])
      return base(path)
    })
  })

  it('clears the account filter when it would hide the payment, and opens it in its month', async () => {
    localStorage.setItem('fc_account_id', 'acc_k')
    renderPage(['/transactions?txn=t9'])
    expect(await screen.findByText('UPI/OLDSHOP/509/q')).toBeInTheDocument()
    await waitFor(() => {
      const last = callsTo('/transactions?').at(-1)
      expect(last).toMatch(/month=2026-10/)
      expect(last).not.toMatch(/account_id=/)
    })
    expect(screen.getByRole('combobox', { name: 'Account' })).toHaveValue('')
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
  })

  it('keeps the account filter when the payment is in that account', async () => {
    localStorage.setItem('fc_account_id', 'acc_s')
    renderPage(['/transactions?txn=t9'])
    expect(await screen.findByText('UPI/OLDSHOP/509/q')).toBeInTheDocument()
    await waitFor(() => expect(callsTo('/transactions?').at(-1)).toMatch(/account_id=acc_s/))
    expect(screen.getByRole('combobox', { name: 'Account' })).toHaveValue('acc_s')
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
  })
})

describe('Transactions on open', () => {
  it('asks for the list once, with the scope it will show', async () => {
    renderPage()
    await screen.findAllByLabelText('Select payment')
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
    // Not once with a remembered scope and again when the statements and months arrive.
    expect(callsTo('/transactions?')).toHaveLength(1)
    expect(callsTo('/transactions?')[0]).toMatch(/month=2026-11/)
    expect(callsTo('/transactions/summary')).toHaveLength(1)
  })

  it('asks once with a remembered month too', async () => {
    localStorage.setItem('fc_period_month', '2026-10')
    renderPage()
    await screen.findAllByLabelText('Select payment')
    expect(callsTo('/transactions?')).toHaveLength(1)
    expect(callsTo('/transactions?')[0]).toMatch(/month=2026-10/)
  })

  it('asks once for a cold deep link, with the filter already applied', async () => {
    // A returning user: the period is remembered, so it is decided as soon as the statements arrive.
    localStorage.setItem('fc_period_month', '2026-11')
    accountsServed = TWO_ACCOUNTS
    localStorage.setItem('fc_account_id', 'acc_k')
    renderPage(['/transactions?category=Uncategorized'])
    await screen.findAllByLabelText('Select payment')
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
    expect(callsTo('/transactions?')).toHaveLength(1)
    expect(callsTo('/transactions?')[0]).toMatch(/category=Uncategorized/)
    // A link that names no account means every account: the remembered filter it clears is not in the one ask either.
    expect(callsTo('/transactions?')[0]).not.toMatch(/account_id=/)
    expect(screen.getByRole('combobox', { name: 'Account' })).toHaveValue('')
    expect(callsTo('/transactions/summary')).toHaveLength(1)
  })

  it('asks once for a drill-through that names its account, and the filter becomes that account', async () => {
    accountsServed = TWO_ACCOUNTS
    localStorage.setItem('fc_period_month', '2026-11')
    renderPage(['/transactions?category=Food%20%26%20Dining&month=2026-11&account=acc_s'])
    await screen.findAllByLabelText('Select payment')
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
    expect(callsTo('/transactions?')).toHaveLength(1)
    expect(callsTo('/transactions?')[0]).toMatch(/account_id=acc_s/)
    expect(callsTo('/transactions/summary')).toHaveLength(1)
    expect(screen.getByRole('combobox', { name: 'Account' })).toHaveValue('acc_s')
  })

  it('asks once for a jump from another page to a search link', async () => {
    let go
    function Jump() {
      go = useNavigate()
      return null
    }
    render(
      <MemoryRouter initialEntries={['/elsewhere']}>
        <PrivacyProvider>
          <LedgerProvider>
            <PeriodProvider>
              <AnnotationJobProvider>
                <Jump />
                <Routes>
                  <Route path="/elsewhere" element={<p>Elsewhere</p>} />
                  <Route path="/transactions" element={<Transactions />} />
                </Routes>
              </AnnotationJobProvider>
            </PeriodProvider>
          </LedgerProvider>
        </PrivacyProvider>
      </MemoryRouter>
    )
    await screen.findByText('Elsewhere')
    // The accounts and months are in, as after a visit to another page.
    await waitFor(() => expect(callsTo('/transactions/months').length).toBeGreaterThan(0))
    await act(async () => { go('/transactions?q=UPI') })
    await screen.findAllByLabelText('Select payment')
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
    expect(callsTo('/transactions?')).toHaveLength(1)
    expect(callsTo('/transactions?')[0]).toMatch(/q=UPI/)
  })

  it('shows loading, never "Add a statement to get started", while it waits for the accounts', async () => {
    let release
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => (path === '/accounts' ? new Promise(r => { release = () => r(ONE_ACCOUNT) }) : base(path)))
    renderPage()
    expect(await screen.findByText('Loading…')).toBeInTheDocument()
    expect(screen.queryByText('Add a statement to get started.')).not.toBeInTheDocument()
    expect(callsTo('/transactions?')).toHaveLength(0)
    release()
    await screen.findAllByLabelText('Select payment')
    expect(callsTo('/transactions?')).toHaveLength(1)
  })
})

describe('Transactions unpairing a transfer', () => {
  const out = (paired) => ({
    id: 'p1', statement_id: 's2', txn_date: '2026-11-03', amount: 5000, debit_credit: 'debit',
    raw_description: 'IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS', counterparty_key: 'TO OWN', payee: 'To Own Sbi Savings',
    payee_all: 'To Own Sbi Savings', note: null, channel: 'IMPS', tags: [],
    ...(paired
      ? { annotation_id: 'a1', category: 'Self Transfers', subcategory: 'Self Transfer', merchant: null, confidence: 1, source: 'pair',
          transfer: { transaction_id: 'p2', direction: 'to', account_label: 'SBI savings', txn_date: '2026-11-03', text: 'Moved to SBI savings' } }
      : { annotation_id: null, transfer: null }),
  })
  let paired
  let unpairBody

  beforeEach(() => {
    paired = true
    unpairBody = { unpaired: ['p1', 'p2'], to_sort: ['p1', 'p2'], account_labels: { p1: 'Kotak savings', p2: 'SBI savings' }, undo_token: 'tok' }
    const base = mockApi.get.getMockImplementation()
    mockApi.get.mockImplementation(path => {
      if (path === '/transactions/p1') return Promise.resolve(out(paired))
      if (path === '/transactions/p2') return Promise.resolve({ ...out(paired), id: 'p2', debit_credit: 'credit', payee: 'From Kotak' })
      if (path.startsWith('/transactions?')) return Promise.resolve([out(paired)])
      return base(path)
    })
    mockApi.post.mockImplementation(async path => {
      if (path === '/transfers/unpair') { paired = false; return unpairBody }
      if (path === '/annotations/review-queue/undo') { paired = true; return { restored: ['p1', 'p2'] } }
      return {}
    })
  })

  it('says it is paired on the row and in the panel, and Unpair takes both away', async () => {
    renderPage(['/transactions?txn=p1'])
    expect(await screen.findByText(/Paired with the payment on 3 Nov in SBI savings/)).toBeInTheDocument()
    expect(screen.getAllByText('Moved to SBI savings').length).toBeGreaterThan(1) // the row and the panel line

    await userEvent.setup().click(screen.getByRole('button', { name: 'Unpair' }))
    await waitFor(() => expect(screen.queryByText(/Paired with the payment/)).not.toBeInTheDocument())
    expect(mockApi.post).toHaveBeenCalledWith('/transfers/unpair', { transaction_id: 'p1' })
    // The row does not say it is moved any more, and the open panel offers to sort it.
    expect(screen.queryByText('Moved to SBI savings')).not.toBeInTheDocument()
    expect(await screen.findByText('Sort this payment')).toBeInTheDocument()
    const [message, type, options] = toastSpy.mock.calls.at(-1)
    expect(message).toBe('Unpaired. Both payments are waiting in Teach Me.')
    expect(type).toBe('success')
    expect(options.action.label).toBe('Undo')
  })

  it.each([
    ['one side the user had labelled keeps its label', ['p2'], 'Unpaired. The SBI savings payment is waiting in Teach Me.'],
    ['the clicked side is the one waiting', ['p1'], 'Unpaired. The Kotak savings payment is waiting in Teach Me.'],
    ['neither is waiting', [], 'Unpaired.'],
  ])('the toast says what is waiting in Teach Me when %s', async (_name, toSort, expected) => {
    unpairBody = { ...unpairBody, to_sort: toSort }
    renderPage(['/transactions?txn=p1'])
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Unpair' }))
    await waitFor(() => expect(toastSpy.mock.calls.at(-1)[0]).toBe(expected))
  })

  it('calling the payment something else re-reads the payment it was paired with', async () => {
    mockApi.patch.mockImplementation(async () => {
      paired = false
      return { id: 'a1', category: 'Food & Dining', subcategory: null, merchant: null, tags: [], confidence: 1, source: 'manual' }
    })
    const user = userEvent.setup()
    renderPage(['/transactions?txn=p1'])
    await screen.findByText(/Paired with the payment on 3 Nov/)
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Category' }), 'Food & Dining')
    mockApi.get.mockClear()
    await user.click(screen.getByRole('button', { name: 'Update' }))
    await waitFor(() => expect(callsTo('/transactions/p2')).toHaveLength(1))
  })

  it('Undo from the toast pairs them again and the row says so again', async () => {
    renderPage(['/transactions?txn=p1'])
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Unpair' }))
    await screen.findByText('Sort this payment')
    const { action } = toastSpy.mock.calls.at(-1)[2]
    await act(async () => action.onClick())
    expect(mockApi.post).toHaveBeenLastCalledWith('/annotations/review-queue/undo', { token: 'tok' })
    expect(await screen.findByText(/Paired with the payment on 3 Nov in SBI savings/)).toBeInTheDocument()
    expect(toastSpy.mock.calls.at(-1)[0]).toBe('Paired again')
  })

  it('says so when the unpair fails and leaves the pair as it was', async () => {
    mockApi.post.mockRejectedValue(Object.assign(new Error('That payment is not paired.'), { status: 409 }))
    renderPage(['/transactions?txn=p1'])
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Unpair' }))
    await waitFor(() => expect(toastSpy.mock.calls.at(-1)[1]).toBe('error'))
    expect(screen.getByText(/Paired with the payment on 3 Nov/)).toBeInTheDocument()
  })
})
