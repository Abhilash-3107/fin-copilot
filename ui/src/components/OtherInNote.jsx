import Amount from './Amount.jsx'

// The notes under the In tile that say what part of In was not earned income:
// credits sorted as refunds and paybacks (`amount`), and credits not sorted yet
// (`unsorted`), which are never called refunds. One component so Dashboard and
// Money Map word them the same way, and the amounts go through Amount so
// privacy mode hides them.
export default function OtherInNote({ amount, unsorted, className = 'text-xs text-[#94a3b8]' }) {
  const hasRefunds = amount > 0
  const hasUnsorted = unsorted > 0
  if (!hasRefunds && !hasUnsorted) return null
  return (
    <>
      {hasRefunds && (
        <span className={`block ${className}`}>
          incl <Amount value={amount} decimals={0} /> in refunds and paybacks
        </span>
      )}
      {hasUnsorted && (
        <span className={`block ${className}`}>
          incl <Amount value={unsorted} decimals={0} /> not sorted yet
        </span>
      )}
    </>
  )
}
