import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import Amount, { formatRupees } from './Amount.jsx'

function renderWithPrivacy(ui) {
  return render(<PrivacyProvider>{ui}</PrivacyProvider>)
}

beforeEach(() => {
  localStorage.clear()
})

describe('formatRupees', () => {
  it('formats a plain magnitude with en-IN grouping', () => {
    expect(formatRupees(123456.5, { decimals: 2 })).toBe('₹1,23,456.50')
  })

  it('drops decimals when asked', () => {
    expect(formatRupees(1234.9, { decimals: 0 })).toBe('₹1,235')
  })

  it('prefixes a minus for debits and a plus for credits', () => {
    expect(formatRupees(250, { decimals: 2, debitCredit: 'debit' })).toBe('−₹250.00')
    expect(formatRupees(250, { decimals: 2, debitCredit: 'credit' })).toBe('+₹250.00')
  })

  it('renders the magnitude regardless of the raw value sign', () => {
    expect(formatRupees(-250, { decimals: 0, debitCredit: 'debit' })).toBe('−₹250')
  })
})

describe('<Amount>', () => {
  it('renders the formatted amount when privacy mode is off', () => {
    renderWithPrivacy(<Amount value={2500} decimals={0} />)
    expect(screen.getByText('₹2,500')).toBeInTheDocument()
  })

  it('keeps the value in the DOM but blurred when privacy mode is on', () => {
    // localStorage seeds the provider as hidden.
    localStorage.setItem('fc_privacy_hidden', '1')
    renderWithPrivacy(<Amount value={2500} decimals={0} />)

    const el = screen.getByText('₹2,500')
    expect(el).toHaveClass('blur-[5px]')
    expect(el).toHaveClass('select-none')
    expect(el).toHaveAttribute('aria-label', 'Amount hidden')
  })

  it('toggles blur when the Shift+P shortcut fires', () => {
    renderWithPrivacy(<Amount value={2500} decimals={0} />)

    expect(screen.getByText('₹2,500')).not.toHaveClass('blur-[5px]')

    fireEvent.keyDown(window, { key: 'P', shiftKey: true })
    expect(screen.getByText('₹2,500')).toHaveClass('blur-[5px]')

    fireEvent.keyDown(window, { key: 'P', shiftKey: true })
    expect(screen.getByText('₹2,500')).not.toHaveClass('blur-[5px]')
  })
})
