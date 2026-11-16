import { StrictMode } from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
import { MemoryRouter } from '../test/router.jsx'

const { mockApi } = vi.hoisted(() => ({
  mockApi: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))
vi.mock('../lib/api.js', () => ({ api: mockApi, ApiError: class extends Error {} }))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import CommandPalette from './CommandPalette.jsx'

function renderPalette() {
  return render(
    <PrivacyProvider>
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>
    </PrivacyProvider>
  )
}

function openPalette() {
  fireEvent.keyDown(window, { key: 'k', metaKey: true })
}

describe('CommandPalette', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/transactions')) return Promise.resolve([
        { id: 't1', txn_date: '2026-01-05', amount: 1200, debit_credit: 'debit', raw_description: 'UPI/SWIGGY/5015/order', merchant: 'Swiggy', payee: 'Swiggy' },
      ])
      if (path.startsWith('/people')) return Promise.resolve([{ id: 'p1', name: 'Karabi', upi: 'karabi' }])
      if (path.startsWith('/groups')) return Promise.resolve([{ id: 'g1', name: 'Goa Trip' }])
      return Promise.resolve([])
    })
  })

  it('is hidden until Cmd-K is pressed', () => {
    renderPalette()
    expect(screen.queryByPlaceholderText(/Search payments/)).not.toBeInTheDocument()
    openPalette()
    expect(screen.getByPlaceholderText(/Search payments/)).toBeInTheDocument()
  })

  it('shows jump-to-page results with no query', () => {
    renderPalette()
    openPalette()
    expect(screen.getByText('Go to')).toBeInTheDocument()
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  it('searches across transactions, people and groups', async () => {
    renderPalette()
    openPalette()
    fireEvent.change(screen.getByPlaceholderText(/Search payments/), { target: { value: 'swiggy' } })
    await waitFor(() => expect(screen.getByText('Transactions')).toBeInTheDocument())
    expect(screen.getByText('Swiggy')).toBeInTheDocument()
    expect(screen.getByText('Karabi')).toBeInTheDocument()
    expect(screen.getByText('Goa Trip')).toBeInTheDocument()
  })

  it('puts the cursor in the search field under React StrictMode, so typing works in dev', async () => {
    render(
      <StrictMode>
        <PrivacyProvider>
          <MemoryRouter>
            <button>Search</button>
            <CommandPalette />
          </MemoryRouter>
        </PrivacyProvider>
      </StrictMode>,
    )
    // Opened from a control (the top bar's search field), so there is an opener whose focus a remount could restore.
    screen.getByRole('button', { name: 'Search' }).focus()
    openPalette()
    const input = screen.getByPlaceholderText(/Search payments/)
    expect(input).toHaveFocus()
    fireEvent.change(input, { target: { value: 'swiggy' } })
    expect(await screen.findByText('Karabi')).toBeInTheDocument()
  })

  it('closes on Escape', () => {
    renderPalette()
    openPalette()
    fireEvent.keyDown(screen.getByPlaceholderText(/Search payments/), { key: 'Escape' })
    expect(screen.queryByPlaceholderText(/Search payments/)).not.toBeInTheDocument()
  })
})

describe('CommandPalette and privacy mode', () => {
  function search() {
    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    fireEvent.change(screen.getByPlaceholderText(/Search payments/), { target: { value: 'swiggy' } })
  }

  beforeEach(() => {
    localStorage.clear()
    mockApi.get.mockImplementation(path => Promise.resolve(path.startsWith('/transactions')
      ? [{ id: 't1', txn_date: '2026-01-05', amount: 1200, debit_credit: 'debit', raw_description: 'UPI/SWIGGY/5015/order', merchant: 'Swiggy', payee: 'Swiggy' }]
      : []))
  })

  it('shows the amount of a payment it finds', async () => {
    renderPalette()
    search()
    const amount = await screen.findByText('−₹1,200')
    expect(amount).not.toHaveClass('blur-[5px]')
  })

  it('blurs and labels that amount when privacy mode is on', async () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    renderPalette()
    search()
    const amount = await screen.findByText('−₹1,200')
    expect(amount).toHaveClass('blur-[5px]')
    expect(amount).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByText('Amount hidden')).toBeInTheDocument()
  })
})

describe('CommandPalette results', () => {
  const SWIGGY = (id, day, amount, extra = {}) => ({
    id, txn_date: `2026-01-${day}`, amount, debit_credit: 'debit', raw_description: `UPI/SWIGGY/${id}`,
    merchant: 'Swiggy', payee: 'Swiggy', payee_all: 'Swiggy', counterparty_key: 'SWIGGY', ...extra,
  })

  function Where() {
    const { pathname, search } = useLocation()
    return <p data-testid="where">{pathname + search}</p>
  }

  function renderWithLocation() {
    return render(
      <PrivacyProvider>
        <MemoryRouter initialEntries={['/dashboard']}>
          <CommandPalette />
          <Where />
        </MemoryRouter>
      </PrivacyProvider>,
    )
  }

  async function search(term) {
    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: term } })
    await waitFor(() => expect(screen.queryAllByRole('option').length).toBeGreaterThan(0))
  }

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mockApi.get.mockImplementation(path => {
      if (path.startsWith('/transactions')) return Promise.resolve([
        SWIGGY('t1', '05', 1200), SWIGGY('t2', '09', 300), SWIGGY('t3', '12', 450),
        { id: 't4', txn_date: '2026-01-20', amount: 90, debit_credit: 'debit', raw_description: 'UPI/NEHA', payee: 'Neha Gupta', payee_all: 'Neha Gupta', counterparty_key: 'NEHA GUPTA' },
      ])
      if (path.startsWith('/people')) return Promise.resolve([{ id: 'p1', name: 'Neha Gupta', upi: 'neha.gupta@paytm' }])
      if (path.startsWith('/groups')) return Promise.resolve([])
      return Promise.resolve([])
    })
  })

  it('is a labelled dialog with a combobox over a listbox, and the arrows move aria-activedescendant', async () => {
    renderWithLocation()
    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    expect(screen.getByRole('dialog', { name: 'Search' })).toHaveAttribute('aria-modal', 'true')
    const box = screen.getByRole('combobox')
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    const first = screen.getAllByRole('option')[0]
    expect(box).toHaveAttribute('aria-activedescendant', first.id)
    expect(first).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    const second = screen.getAllByRole('option')[1]
    expect(box).toHaveAttribute('aria-activedescendant', second.id)
    expect(second).toHaveAttribute('aria-selected', 'true')
  })

  it('opens from the top bar event too', async () => {
    renderWithLocation()
    act(() => { window.dispatchEvent(new Event('fc:open-palette')) })
    expect(await screen.findByRole('dialog', { name: 'Search' })).toBeInTheDocument()
  })

  it('opens the exact payment, not a text search', async () => {
    renderWithLocation()
    await search('swiggy')
    fireEvent.click(screen.getByRole('option', { name: /05 Jan/ }))
    expect(screen.getByTestId('where')).toHaveTextContent('/transactions?txn=t1')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows repeated payees once, as a result that filters to the payee across all months', async () => {
    renderWithLocation()
    await search('swiggy')
    const payee = screen.getByRole('option', { name: /Swiggy - all payments/ })
    fireEvent.click(payee)
    const where = screen.getByTestId('where').textContent
    expect(where).toMatch(/^\/transactions\?/)
    const params = new URLSearchParams(where.split('?')[1])
    expect(params.getAll('payee_key')).toEqual(['SWIGGY'])
    expect(params.get('month')).toBe('all')
    expect(params.has('q')).toBe(false)
  })

  it('does not summarise a payee that appears once', async () => {
    renderWithLocation()
    await search('neha')
    expect(screen.queryByRole('option', { name: /1 payments?\b/ })).not.toBeInTheDocument()
  })

  it('a person result filters to their payments across all months, never a text search', async () => {
    renderWithLocation()
    await search('neha')
    fireEvent.click(screen.getByRole('option', { name: /Neha Gupta\s*person/ }))
    const params = new URLSearchParams(screen.getByTestId('where').textContent.split('?')[1])
    expect(params.get('person')).toBe('neha.gupta@paytm')
    expect(params.get('month')).toBe('all')
    expect(params.has('q')).toBe(false)
  })

  it('says search failed, quietly, with a Retry', async () => {
    mockApi.get.mockImplementation(() => Promise.reject(new TypeError('Failed to fetch')))
    renderWithLocation()
    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'swiggy' } })
    expect(await screen.findByText(/Couldn't search just now/)).toBeInTheDocument()
    expect(screen.queryByText('No matches.')).not.toBeInTheDocument()
    mockApi.get.mockImplementation(() => Promise.resolve([]))
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('No matches.')).toBeInTheDocument()
  })

  it('gives focus back to what had it when it closes', () => {
    render(
      <PrivacyProvider>
        <MemoryRouter>
          <button>Before</button>
          <CommandPalette />
        </MemoryRouter>
      </PrivacyProvider>,
    )
    const before = screen.getByRole('button', { name: 'Before' })
    before.focus()
    fireEvent.keyDown(window, { key: 'k', metaKey: true })
    expect(screen.getByRole('combobox')).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    expect(before).toHaveFocus()
  })
})
