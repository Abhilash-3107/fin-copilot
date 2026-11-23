import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from '../test/router.jsx'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import BooksGrid, { coverageText, checkText } from './BooksGrid.jsx'

const cell = (over = {}) => ({
  gap: false, payments: 41, statement_ids: ['s1'], covered: [['2026-10-01', '2026-10-31']], full: true,
  balance_check: { checked: 41, consistent: 41 }, ...over,
})

const BOOKS = {
  months: ['2026-09', '2026-10', '2026-11'],
  accounts: [
    {
      id: 'a1', account_label: 'Kotak savings', balance: 90770, balance_as_of: '2026-11-30',
      cells: {
        '2026-09': cell({ payments: 30, balance_check: { checked: 30, consistent: 30 }, covered: [['2026-09-01', '2026-09-30']] }),
        '2026-10': cell(),
        '2026-11': cell({ payments: 20, balance_check: { checked: 20, consistent: 19 }, covered: [['2026-11-01', '2026-11-30']] }),
      },
    },
    {
      id: 'a2', account_label: 'SBI savings', balance: null, balance_as_of: null,
      cells: {
        '2026-09': cell({ payments: 4, covered: [['2026-09-01', '2026-09-30']], balance_check: { checked: 0, consistent: 0 } }),
        '2026-10': { gap: true, payments: 0 },
        '2026-11': cell({ payments: 3, full: false, covered: [['2026-11-01', '2026-11-15']], balance_check: { checked: 0, consistent: 0 } }),
      },
    },
  ],
  transfers: [
    { transaction_id: 't1', txn_date: '2026-10-28', arrived: '2026-10-28', amount: 10000, from_label: 'Kotak savings', to_label: 'SBI savings' },
  ],
}

function renderGrid(props = {}) {
  const handlers = { onShowStatements: vi.fn(), onRename: vi.fn().mockResolvedValue(true) }
  render(
    <MemoryRouter>
      <PrivacyProvider><BooksGrid books={BOOKS} selected={null} {...handlers} {...props} /></PrivacyProvider>
    </MemoryRouter>,
  )
  return handlers
}

describe('coverage and check words', () => {
  it('says full, to a day, from a day, between two days, and how many statements', () => {
    expect(coverageText(cell(), '2026-10')).toBe('Full month')
    expect(coverageText(cell({ full: false, covered: [['2026-11-01', '2026-11-15']] }), '2026-11')).toBe('to 15 Nov')
    expect(coverageText(cell({ full: false, covered: [['2026-10-10', '2026-10-31']] }), '2026-10')).toBe('from 10 Oct')
    expect(coverageText(cell({ full: false, covered: [['2026-10-10', '2026-10-20']] }), '2026-10')).toBe('10 to 20 Oct')
    expect(coverageText(cell({ statement_ids: ['a', 'b'] }), '2026-10')).toBe('Full month, 2 statements')
    expect(coverageText(cell({ full: false, covered: [['2026-10-01', '2026-10-10'], ['2026-10-20', '2026-10-31']] }), '2026-10'))
      .toBe('to 10 Oct, from 20 Oct')
  })

  it('says the balances check out, or that there are none to check', () => {
    expect(checkText({ checked: 41, consistent: 41 })).toBe('41 of 41 balances check out')
    expect(checkText({ checked: 20, consistent: 19 })).toBe('19 of 20 balances check out')
    expect(checkText({ checked: 0, consistent: 0 })).toBe('No balances to check')
  })
})

describe('BooksGrid', () => {
  it('has one row per account and one column per month, newest first', () => {
    renderGrid()
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toEqual(['Account', 'Nov 2026', 'Oct 2026', 'Sep 2026'])
    expect(screen.getAllByRole('rowheader').map(h => h.textContent)).toEqual([
      expect.stringContaining('Kotak savings'), expect.stringContaining('SBI savings'),
    ])
  })

  it('shows a month with no statement as a gap that says so', () => {
    renderGrid()
    const gap = screen.getByRole('img', { name: 'SBI savings, October 2026, no statement' })
    expect(gap).toHaveTextContent('No statement')
  })

  it('says how much of a month is covered, and how many balances check out', () => {
    renderGrid()
    const partial = screen.getByRole('link', { name: 'SBI savings, November 2026, 3 payments, no balances to check' })
    expect(partial).toHaveTextContent('to 15 Nov')
    expect(partial).toHaveTextContent('No balances to check')
    const balanced = screen.getByRole('link', { name: 'Kotak savings, October 2026, 41 payments, balanced' })
    expect(balanced).toHaveTextContent('41 of 41 balances check out')
    expect(balanced).toHaveTextContent('Full month')
    expect(screen.getByRole('link', { name: 'Kotak savings, November 2026, 20 payments, not fully balanced' }))
      .toHaveTextContent('19 of 20 balances check out')
  })

  it('opens Transactions for the account and the month from a cell, by link so the keyboard reaches it', () => {
    renderGrid()
    const link = screen.getByRole('link', { name: /Kotak savings, October 2026/ })
    expect(link.getAttribute('href')).toBe('/transactions?month=2026-10&account=a1')
  })

  it('shows the latest balance with its date, and none for an account without one', () => {
    renderGrid()
    const row = screen.getByRole('rowheader', { name: /Kotak savings/ })
    expect(row).toHaveTextContent('₹90,770 as of 30 Nov 2026')
    expect(screen.getByRole('rowheader', { name: /SBI savings/ })).not.toHaveTextContent('as of')
  })

  it('picks a cell\'s statements out for the list below', async () => {
    const { onShowStatements } = renderGrid()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Statements in Kotak savings, October 2026' }))
    expect(onShowStatements).toHaveBeenCalledWith('a1', '2026-10')
  })

  it('marks the picked cell pressed', () => {
    renderGrid({ selected: { accountId: 'a1', month: '2026-10' } })
    expect(screen.getByRole('button', { name: 'Statements in Kotak savings, October 2026' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('lists the moves between the accounts, not counted', () => {
    renderGrid()
    expect(screen.getByRole('listitem')).toHaveTextContent('Moved ₹10,000 from Kotak savings to SBI savings on 28 Oct, not counted')
  })

  it('leaves months before an account\'s first statement blank', () => {
    const books = {
      ...BOOKS,
      accounts: [{ ...BOOKS.accounts[0], cells: { '2026-11': BOOKS.accounts[0].cells['2026-11'] } }],
    }
    render(<MemoryRouter><PrivacyProvider><BooksGrid books={books} selected={null} onShowStatements={vi.fn()} onRename={vi.fn()} /></PrivacyProvider></MemoryRouter>)
    expect(screen.queryByText('No statement')).not.toBeInTheDocument()
    expect(screen.getAllByRole('link')).toHaveLength(1)
  })

  it('renames an account in place: Enter saves, and focus returns to the control', async () => {
    const { onRename } = renderGrid()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Rename Kotak savings' }))
    const input = screen.getByRole('textbox', { name: 'Name this account' })
    expect(input).toHaveValue('Kotak savings')
    await user.clear(input)
    await user.type(input, 'Salary{Enter}')
    expect(onRename).toHaveBeenCalledWith('a1', 'Salary')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rename Kotak savings' })).toHaveFocus())
  })

  it('does not turn the words already shown into a name', async () => {
    const { onRename } = renderGrid()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Rename SBI savings' }))
    await user.type(screen.getByRole('textbox', { name: 'Name this account' }), '{Enter}')
    expect(onRename).not.toHaveBeenCalled()
  })

  it('Escape cancels a rename', async () => {
    const { onRename } = renderGrid()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Rename Kotak savings' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(onRename).not.toHaveBeenCalled()
  })

  it('draws nothing before there is an account', () => {
    const { container } = render(
      <MemoryRouter><BooksGrid books={{ months: [], accounts: [], transfers: [] }} selected={null} onShowStatements={vi.fn()} onRename={vi.fn()} /></MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
