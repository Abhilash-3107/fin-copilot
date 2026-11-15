import { usePrivacy } from '../contexts/PrivacyContext.jsx'

// Single source of truth for rendering a rupee amount as a string.
// `debitCredit` adds the signed prefix used in the transaction views; when it
// is omitted, a negative value still carries its − (a Kept tile in a month
// that went over, a category net after a big refund) but positives stay bare.
export function formatRupees(amount, { decimals = 2, debitCredit } = {}) {
  const n = Number(amount)
  const magnitude = Math.abs(n).toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
  if (debitCredit === 'debit') return `−₹${magnitude}`
  if (debitCredit === 'credit') return `+₹${magnitude}`
  return n < 0 ? `−₹${magnitude}` : `₹${magnitude}`
}

// What a hidden figure becomes where it has to be a plain string (a toast, a
// chart tooltip or tick) and cannot be blurred in place.
export const HIDDEN_FIGURE = '••••'
// Rupee amounts inside free text we do not build ourselves, such as an error
// or warning the server wrote.
export const maskRupees = text => text.replace(/[−+-]?₹\s?\d[\d,]*(\.\d+)?/g, HIDDEN_FIGURE)
const HIDDEN_TITLE = 'Amounts hidden - press Shift+P to reveal'

// Renders a rupee amount, honoring the global privacy mode. When privacy is on
// the value is blurred in place (width and layout preserved) instead of being
// swapped for a placeholder, so it reads as deliberately hidden rather than
// still loading.
export default function Amount({ value, debitCredit, decimals = 2, className = '' }) {
  const { hidden } = usePrivacy()
  const text = formatRupees(value, { decimals, debitCredit })

  if (hidden) {
    return (
      <span
        className={`blur-[5px] select-none ${className}`}
        aria-label="Amount hidden"
        title={HIDDEN_TITLE}
      >
        {text}
      </span>
    )
  }

  return <span className={className}>{text}</span>
}

// A share of money, such as the kept rate: as private as the amounts it is
// computed from. `value` is a fraction (0.31 renders 31%). Not for confidence
// or accuracy, which say nothing about money.
export function Percent({ value, className = '' }) {
  const { hidden } = usePrivacy()
  const text = `${Math.round(value * 100)}%`

  if (hidden) {
    return (
      <span
        className={`blur-[5px] select-none ${className}`}
        aria-label="Percentage hidden"
        title={HIDDEN_TITLE}
      >
        {text}
      </span>
    )
  }

  return <span className={className}>{text}</span>
}

// Privacy-safe money strings for the places JSX cannot reach: toast messages,
// chart tooltips and axis ticks. `money` takes formatRupees' arguments; `tick`
// is the compact label for a chart axis.
export function useMoney() {
  const { hidden } = usePrivacy()
  return {
    money: (amount, options) => (hidden ? HIDDEN_FIGURE : formatRupees(amount, options)),
    tick: value => (hidden ? HIDDEN_FIGURE : `₹${Math.round(value / 1000)}k`),
  }
}
