import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import TransactionTable from './TransactionTable.jsx'
import TransactionCards from './TransactionCards.jsx'

const BASE = {
  txn_date: '2026-01-15', amount: 250, debit_credit: 'debit', upi_meta: null,
  payee: 'Chai Wala Corner', note: null, channel: null,
}
const ROWS = [
  { ...BASE, id: 't1', raw_description: 'UPI/CHAI WALA CORNER/501571919522/PAYTM QR/chai', note: 'chai and samosa', channel: 'UPI' },
  { ...BASE, id: 't2', raw_description: 'NEFT/HDFC CREDIT CARD PAYMENT', payee: 'Rent', channel: 'NEFT' },
  { ...BASE, id: 't3', raw_description: 'SOME BANK LINE 123', payee: 'SOME BANK LINE 123', debit_credit: 'credit' },
]

function renderTable(props = {}, Component = TransactionTable) {
  return render(
    <PrivacyProvider>
      <Component transactions={ROWS} onSelect={() => {}} {...props} />
    </PrivacyProvider>
  )
}

describe.each([['TransactionTable', TransactionTable], ['TransactionCards', TransactionCards]])('%s rows', (_name, Component) => {
  const row = text => screen.getByText(text).closest('tr, li')

  it('leads with the payee and puts the note and channel beneath', () => {
    renderTable({}, Component)
    const first = row('Chai Wala Corner')
    expect(within(first).getByText('chai and samosa · UPI')).toBeInTheDocument()
  })

  it('does not print the bank line on the row', () => {
    renderTable({}, Component)
    expect(screen.queryByText(/501571919522/)).not.toBeInTheDocument()
    expect(screen.queryByText(/HDFC CREDIT CARD/)).not.toBeInTheDocument()
  })

  it('shows only the channel when there is no note', () => {
    renderTable({}, Component)
    const second = row('Rent')
    expect(within(second).getByText('NEFT')).toBeInTheDocument()
  })

  it('falls back to the printed line as the lead when nothing was parsed, with no second line', () => {
    renderTable({}, Component)
    const third = row('SOME BANK LINE 123')
    expect(within(third).queryByText(/·/)).not.toBeInTheDocument()
  })

  it('shows a debit in ink and a credit green with a plus', () => {
    renderTable({}, Component)
    const debit = within(row('Rent')).getByText('−₹250.00')
    const credit = within(row('SOME BANK LINE 123')).getByText('+₹250.00')
    expect(debit.className).not.toMatch(/red/)
    expect(credit).toHaveClass('text-green-400')
  })

  it('opens the row on click', async () => {
    const onSelect = vi.fn()
    renderTable({ onSelect }, Component)
    await userEvent.setup().click(screen.getByText('Rent'))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 't2' }))
  })
})

describe.each([['TransactionTable', TransactionTable], ['TransactionCards', TransactionCards]])('%s paired rows', (_name, Component) => {
  const PAIRED = [
    {
      ...BASE, id: 'p1', raw_description: 'IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS', payee: 'To Own Sbi Savings',
      channel: 'IMPS', note: null,
      transfer: { transaction_id: 'p2', direction: 'to', account_label: 'SBI savings', txn_date: '2026-01-15', text: 'Moved to SBI savings' },
    },
    {
      ...BASE, id: 'p2', raw_description: 'IMPS/FROM KOTAK', payee: 'From Kotak', debit_credit: 'credit', channel: 'IMPS', note: 'monthly',
      transfer: { transaction_id: 'p1', direction: 'from', account_label: 'Kotak savings', txn_date: '2026-01-15', text: 'Moved from Kotak savings' },
    },
  ]

  it('says where the money went before the note and channel', () => {
    render(<PrivacyProvider><Component transactions={PAIRED} onSelect={() => {}} /></PrivacyProvider>)
    expect(screen.getByText('Moved to SBI savings').parentElement).toHaveTextContent('Moved to SBI savings · IMPS')
    expect(screen.getByText('Moved from Kotak savings').parentElement).toHaveTextContent('Moved from Kotak savings · monthly · IMPS')
  })

  it('says nothing extra on a payment that is not paired', () => {
    renderTable({}, Component)
    expect(screen.queryByText(/Moved/)).not.toBeInTheDocument()
  })

  it('shows no confidence for a pair, as for a hand label', () => {
    const annotationMap = { p1: { category: 'Self Transfers', subcategory: 'Self Transfer', source: 'pair', confidence: 1 } }
    render(<PrivacyProvider><Component transactions={PAIRED} annotationMap={annotationMap} onSelect={() => {}} /></PrivacyProvider>)
    expect(screen.getByText('paired')).toBeInTheDocument()
    expect(screen.queryByText('100%')).not.toBeInTheDocument()
  })
})

describe.each([['TransactionTable', TransactionTable], ['TransactionCards', TransactionCards]])('%s rows waiting on a pair', (_name, Component) => {
  const WAITING = [
    { ...BASE, id: 'w1', raw_description: 'NEFT/N123456789/ABHI B', payee: 'Abhi B', channel: 'NEFT', waiting_on_pair: true },
    { ...BASE, id: 'w2', raw_description: 'UPI/SWIGGY/1/UPI', payee: 'Swiggy', channel: 'UPI', waiting_on_pair: false },
  ]
  const annotationMap = {
    w1: { category: 'Investments', subcategory: 'Mutual Funds', source: 'llm', confidence: 0.97 },
    w2: { category: 'Food & Dining', subcategory: 'Restaurants', source: 'llm', confidence: 0.97 },
  }
  const renderWaiting = () => render(
    <PrivacyProvider><Component transactions={WAITING} annotationMap={annotationMap} onSelect={() => {}} /></PrivacyProvider>
  )

  it('says in plain words that it is not sorted and keeps the guess as small text', () => {
    renderWaiting()
    const row = screen.getByText('Abhi B').closest('tr, li')
    expect(within(row).getByText('Not sorted - maybe a move between your accounts')).toBeInTheDocument()
    expect(within(row).getByText('Guess was Investments › Mutual Funds')).toBeInTheDocument()
    expect(within(row).queryByText('Investments › Mutual Funds')).not.toBeInTheDocument()
  })

  it('shows no source or confidence of its own: the guess is not an answer', () => {
    renderWaiting()
    const row = screen.getByText('Abhi B').closest('tr, li')
    expect(within(row).getByText('not sorted')).toBeInTheDocument()
    expect(within(row).queryByText('llm')).not.toBeInTheDocument()
    expect(within(row).queryByText('97%')).not.toBeInTheDocument()
  })

  it('shows the category of a payment that is not waiting as before', () => {
    renderWaiting()
    const row = screen.getByText('Swiggy').closest('tr, li')
    expect(within(row).getByText('Food & Dining › Restaurants')).toBeInTheDocument()
    expect(within(row).queryByText(/Not sorted/)).not.toBeInTheDocument()
  })
})

describe('TransactionTable header', () => {
  it('names the first column Payee', () => {
    renderTable()
    expect(screen.getByRole('columnheader', { name: 'Payee' })).toBeInTheDocument()
  })
})
