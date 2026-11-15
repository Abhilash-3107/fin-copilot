// A payment as a list row names it: the payee in ink, and beneath it in grey
// the user's own UPI note and the channel it moved on. The server resolves the
// name (the user's rename, the sorted merchant, the payee as printed, or the
// printed line when nothing parsed), so a row never derives one itself and
// never comes out empty. The printed line itself is on the payment's panel.
export default function PaymentLabel({ txn, className = '' }) {
  const detail = [txn.note, txn.channel].filter(Boolean).join(' · ')
  return (
    <div className={`min-w-0 ${className}`}>
      <div className="truncate text-[#e2e8f0]">{txn.payee || txn.raw_description}</div>
      {detail && <div className="truncate text-xs text-[#94a3b8]">{detail}</div>}
    </div>
  )
}
