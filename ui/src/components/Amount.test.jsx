import { describe, it, expect, beforeEach } from 'vitest'
import { render, renderHook, screen, fireEvent } from '@testing-library/react'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import Amount, { Percent, formatRupees, maskRupees, useMoney, HIDDEN_FIGURE } from './Amount.jsx'

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

  it('keeps the minus on a plain negative value', () => {
    // A Kept tile in a month that went over, or a category net after a big
    // refund, must not read as a positive amount.
    expect(formatRupees(-20543, { decimals: 0 })).toBe('−₹20,543')
    expect(formatRupees(20543, { decimals: 0 })).toBe('₹20,543')
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

describe('<Percent>', () => {
  it('renders a fraction as a whole percentage when privacy mode is off', () => {
    renderWithPrivacy(<Percent value={0.314} />)
    expect(screen.getByText('31%')).not.toHaveClass('blur-[5px]')
  })

  it('blurs and labels the percentage when privacy mode is on', () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    renderWithPrivacy(<Percent value={0.314} />)

    const el = screen.getByText('31%')
    expect(el).toHaveClass('blur-[5px]')
    expect(el).toHaveClass('select-none')
    expect(el).toHaveAttribute('aria-label', 'Percentage hidden')
  })

  it('follows the Shift+P shortcut like an amount does', () => {
    renderWithPrivacy(<Percent value={0.5} />)
    fireEvent.keyDown(window, { key: 'P', shiftKey: true })
    expect(screen.getByText('50%')).toHaveClass('blur-[5px]')
  })
})

describe('useMoney', () => {
  const wrapper = ({ children }) => <PrivacyProvider>{children}</PrivacyProvider>

  it('formats amounts and chart ticks while amounts are shown', () => {
    const { result } = renderHook(() => useMoney(), { wrapper })
    expect(result.current.money(2500, { decimals: 0 })).toBe('₹2,500')
    expect(result.current.tick(12000)).toBe('₹12k')
  })

  it('returns a mask and no digits while amounts are hidden', () => {
    localStorage.setItem('fc_privacy_hidden', '1')
    const { result } = renderHook(() => useMoney(), { wrapper })
    expect(result.current.money(2500, { decimals: 0 })).toBe(HIDDEN_FIGURE)
    expect(result.current.tick(12000)).toBe(HIDDEN_FIGURE)
  })
})

describe('maskRupees', () => {
  it('masks every amount in a sentence, with or without a sign or decimals', () => {
    expect(maskRupees('Opening balance ₹12,000.50 does not match the closing balance −₹1,00,000.'))
      .toBe(`Opening balance ${HIDDEN_FIGURE} does not match the closing balance ${HIDDEN_FIGURE}.`)
  })

  it('leaves text with no amount alone', () => {
    expect(maskRupees('Policy added.')).toBe('Policy added.')
  })
})
