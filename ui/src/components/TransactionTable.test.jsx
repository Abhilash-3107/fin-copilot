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

describe('TransactionTable header', () => {
  it('names the first column Payee', () => {
    renderTable()
    expect(screen.getByRole('columnheader', { name: 'Payee' })).toBeInTheDocument()
  })
})
