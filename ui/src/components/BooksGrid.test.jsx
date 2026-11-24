import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from '../test/router.jsx'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import BooksGrid, { coverageText, checkText, cellCheckText, coverageShort, checkShort } from './BooksGrid.jsx'

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

  it('says "1 balance checks out" for a single balance', () => {
    expect(checkText({ checked: 1, consistent: 1 })).toBe('1 of 1 balance checks out')
    expect(checkText({ checked: 1, consistent: 0 })).toBe('0 of 1 balance checks out')
  })

  it('has a short form for the cell: a word for coverage and a few characters for the balances', () => {
    expect(coverageShort(cell(), '2026-10')).toBe('Full')
    expect(coverageShort(cell({ full: false, covered: [['2026-11-01', '2026-11-15']] }), '2026-11')).toBe('to 15 Nov')
    expect(coverageShort(cell({ full: false, covered: [['2026-10-10', '2026-10-31']] }), '2026-10')).toBe('from 10 Oct')
    expect(coverageShort(cell({ full: false, covered: [['2026-10-10', '2026-10-20']] }), '2026-10')).toBe('10-20 Oct')
    expect(coverageShort(cell({ full: false, covered: [['2026-10-01', '2026-10-10'], ['2026-10-20', '2026-10-31']] }), '2026-10')).toBe('Partial')
    // The short form is a word, never a count: a count of balances is not a count of the payments shown.
    expect(checkShort({ checked: 49, consistent: 49 })).toBe('Balanced')
    expect(checkShort({ checked: 20, consistent: 19 })).toBe('Not balanced')
    expect(checkShort({ checked: 0, consistent: 0 })).toBe('No balances')
  })

  it('words a cell with several statements one statement at a time, never added up', () => {
    const october = { months: null, covered: ['2026-10-01', '2026-10-31'] }
    const several = cell({ statement_checks: [
      { checked: 5, consistent: 5, ...october },
      { checked: 3, consistent: 3, months: null, covered: ['2026-10-14', '2026-10-31'] },
    ] })
    expect(cellCheckText(several, '2026-10'))
      .toBe('Oct 2026 statement: 5 of 5 balances check out; 14 to 31 Oct statement: 3 of 3 balances check out')
    const spanning = cell({ statement_checks: [
      { checked: 150, consistent: 150, months: ['2026-06', '2026-07'], covered: ['2026-07-01', '2026-07-31'] },
      { checked: 5, consistent: 4, ...october, covered: ['2026-07-01', '2026-07-31'] },
    ] })
    expect(cellCheckText(spanning, '2026-07'))
      .toBe('Jun to Jul statement: 150 of 150 balances check out; Jul 2026 statement: 4 of 5 balances check out')
    // A statement over a year end names both years; a lone one-month statement needs no name.
    expect(cellCheckText(cell({ statement_checks: [{ checked: 150, consistent: 150, months: ['2025-12', '2026-02'], covered: ['2026-01-01', '2026-01-31'] }] }), '2026-01'))
      .toBe('Dec 2025 to Feb 2026 statement: 150 of 150 balances check out')
    expect(cellCheckText(cell({ statement_checks: [{ checked: 41, consistent: 41, ...october }] }), '2026-10')).toBe('41 of 41 balances check out')
    expect(cellCheckText(cell({ statement_checks: [{ checked: 1, consistent: 1, months: null, covered: ['2026-10-14', '2026-10-31'] }] }), '2026-10'))
      .toBe('1 of 1 balance checks out')
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
    const partial = screen.getByRole('link', { name: 'SBI savings, November 2026, 3 payments, to 15 Nov, No balances to check' })
    expect(partial).toHaveTextContent('to 15 Nov')
    expect(partial).toHaveTextContent('No balances')
    const balanced = screen.getByRole('link', { name: 'Kotak savings, October 2026, 41 payments, Full month, 41 of 41 balances check out' })
    expect(balanced).toHaveTextContent('Balanced')
    expect(balanced).not.toHaveTextContent(/\d+\/\d+/)
    expect(balanced).toHaveTextContent('Full')
    // The full words are on hover too, so the short form never hides them.
    expect(balanced).toHaveAttribute('title', balanced.getAttribute('aria-label'))
    expect(screen.getByRole('link', { name: 'Kotak savings, November 2026, 20 payments, Full month, 19 of 20 balances check out' }))
      .toHaveTextContent('Not balanced')
  })

  it('words a spanning statement\'s check as the statement\'s in the cell\'s name, and keeps Balanced in the cell', () => {
    const books = {
      ...BOOKS,
      accounts: [{
        ...BOOKS.accounts[0],
        cells: {
          '2026-10': cell({
            payments: 7, balance_check: { checked: 150, consistent: 150 },
            statement_checks: [{ checked: 150, consistent: 150, months: ['2026-08', '2026-10'], covered: ['2026-10-01', '2026-10-31'] }],
          }),
          '2026-09': cell({
            payments: 7, balance_check: { checked: 150, consistent: 147 }, covered: [['2026-09-01', '2026-09-30']],
            statement_checks: [{ checked: 150, consistent: 147, months: ['2026-08', '2026-10'], covered: ['2026-09-01', '2026-09-30'] }],
          }),
        },
      }],
    }
    render(<MemoryRouter><PrivacyProvider><BooksGrid books={books} selected={null} onShowStatements={vi.fn()} onRename={vi.fn()} /></PrivacyProvider></MemoryRouter>)
    const link = screen.getByRole('link', { name: /Kotak savings, October 2026, 7 payments, Full month, Aug to Oct statement: 150 of 150 balances check out/ })
    expect(link).toHaveTextContent('Balanced')
    expect(link).not.toHaveTextContent('150')
    const off = screen.getByRole('link', { name: /Kotak savings, September 2026.*Aug to Oct statement: 147 of 150 balances check out/ })
    expect(off).toHaveTextContent('Not balanced')
  })

  it('words a cell with two statements one at a time and reads one word in the cell', () => {
    const books = {
      ...BOOKS,
      accounts: [{
        ...BOOKS.accounts[0],
        cells: {
          '2026-10': cell({
            payments: 8, statement_ids: ['s1', 's2'], balance_check: { checked: 8, consistent: 8 },
            statement_checks: [
              { checked: 5, consistent: 5, months: null, covered: ['2026-10-01', '2026-10-31'] },
              { checked: 3, consistent: 3, months: null, covered: ['2026-10-14', '2026-10-31'] },
            ],
          }),
        },
      }],
    }
    render(<MemoryRouter><PrivacyProvider><BooksGrid books={books} selected={null} onShowStatements={vi.fn()} onRename={vi.fn()} /></PrivacyProvider></MemoryRouter>)
    const link = screen.getByRole('link', {
      name: 'Kotak savings, October 2026, 8 payments, Full month, 2 statements, Oct 2026 statement: 5 of 5 balances check out; 14 to 31 Oct statement: 3 of 3 balances check out',
    })
    expect(link).toHaveTextContent('Balanced')
    expect(link).not.toHaveTextContent(/\d+\/\d+/)
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
