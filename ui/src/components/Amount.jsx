import { usePrivacy } from '../contexts/PrivacyContext.jsx'

// Single source of truth for rendering a rupee amount as a string.
// `debitCredit` adds the signed prefix used in the transaction views; when it
// is omitted the value is rendered as a plain magnitude (no + / −).
export function formatRupees(amount, { decimals = 2, debitCredit } = {}) {
  const n = Number(amount)
  const magnitude = Math.abs(n).toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
  if (debitCredit === 'debit') return `−₹${magnitude}`
  if (debitCredit === 'credit') return `+₹${magnitude}`
  return `₹${magnitude}`
}

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
        title="Amounts hidden — press Shift+P to reveal"
      >
        {text}
      </span>
    )
  }

  return <span className={className}>{text}</span>
}
