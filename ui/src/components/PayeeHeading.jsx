// The payee, and the note the user typed into their UPI app, as the lead of a
// payment. The API resolves the names: `payee` is this payment's, `payee_all`
// what every payment to the payee is called (use it for anything said about the
// whole group). A payment with no readable payee still arrives with its printed line.
export const payeeName = item => item.payee || item.raw_description
export const groupName = item => item.payee_all || payeeName(item)

export default function PayeeHeading({ item }) {
  return (
    <div className="min-w-0">
      <h2 className="text-xl font-medium text-[#e2e8f0] leading-snug break-words">{payeeName(item)}</h2>
      {item.note && (
        <p className="mt-1 text-sm text-[#94a3b8] break-words">
          {item.debit_credit === 'credit' ? 'Note' : 'Your note'}: <span className="text-[#cbd5e1]">{item.note}</span>
        </p>
      )}
    </div>
  )
}
