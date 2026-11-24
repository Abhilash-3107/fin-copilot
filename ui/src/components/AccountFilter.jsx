import { useLedger } from '../contexts/LedgerContext.jsx'

// The account the Dashboard, Money Map and Transactions look at: every account, or
// one. Shared like the period is, so it follows the user between those pages. It is
// there only when there is a choice to make (two accounts or more).
export default function AccountFilter({ className = '' }) {
  const { accounts, multiple, accountId, setAccountId } = useLedger()
  if (!multiple) return null
  return (
    <select
      value={accountId}
      onChange={e => setAccountId(e.target.value)}
      aria-label="Account"
      className={`bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] cursor-pointer max-w-[14rem] ${className}`}
    >
      <option value="">All accounts</option>
      {accounts.map(a => (
        <option key={a.id} value={a.id}>{a.account_label}</option>
      ))}
    </select>
  )
}
