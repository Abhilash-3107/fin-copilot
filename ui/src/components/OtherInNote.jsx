import Amount from './Amount.jsx'

// The note under the In tile that says how much of In was not earned income.
// One component so Dashboard and Money Map word it the same way, and the
// amount goes through Amount so privacy mode hides it.
export default function OtherInNote({ amount, className = 'text-xs text-[#94a3b8]' }) {
  if (!(amount > 0)) return null
  return (
    <span className={className}>
      incl <Amount value={amount} decimals={0} /> in refunds and paybacks
    </span>
  )
}
