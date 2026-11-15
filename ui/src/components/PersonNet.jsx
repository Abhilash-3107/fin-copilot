import Amount from './Amount.jsx'

// One rendering of "how much more moved one way between you and a person",
// shared by People and Money Map. Words carry the direction (sending money to
// someone is not a loss, so no red/green), and a net that rounds to zero rupees
// reads as even rather than "₹0 more sent".
export default function PersonNet({ net }) {
  const rupees = Math.round(Math.abs(net))
  if (rupees === 0) return <span className="text-[#94a3b8]">Even</span>
  return (
    <span className="text-[#cbd5e1]">
      <Amount value={rupees} decimals={0} /> more {net > 0 ? 'received' : 'sent'}
    </span>
  )
}
