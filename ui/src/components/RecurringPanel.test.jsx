import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const navigate = vi.fn()
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }))

import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import RecurringPanel from './RecurringPanel.jsx'

const ITEMS = [
  { name: 'Rent', keys: ['AMIT.LANDLORD@OKSBI'], category: 'Housing', amount: 15000, months_seen: 3, months_span: 3, cadence: 'monthly', last_date: '2026-03-01', active: true },
  { name: 'Rent', keys: ['SWIGGY'], category: 'Food', amount: 399, months_seen: 3, months_span: 3, cadence: 'monthly', last_date: '2026-03-01', active: true },
]

describe('RecurringPanel', () => {
  it('opens the payments of the row by its key, whatever two rows are called', async () => {
    const user = userEvent.setup()
    render(
      <PrivacyProvider>
        <RecurringPanel items={ITEMS} summary={{ monthly_total: 15399, count: 2 }} />
      </PrivacyProvider>
    )
    const [landlord] = screen.getAllByText('Rent')
    await user.click(landlord)
    expect(navigate).toHaveBeenCalledTimes(1)
    const [path, opts] = navigate.mock.calls[0]
    const params = new URL(path, 'http://x').searchParams
    expect(params.getAll('payee_key')).toEqual(['AMIT.LANDLORD@OKSBI'])
    expect(params.has('merchant')).toBe(false)
    expect(opts.state).toEqual({ payeeName: 'Rent' })
  })
})
