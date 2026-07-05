import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'
import { RefreshCw } from 'lucide-react'
import Amount from './Amount.jsx'
import { txnFilterPath } from '../lib/txnLink.js'

// The committed-money panel on Money Map. Fed by the server's recurrence
// detection (insights.recurring): charges of the same amount from the same
// counterparty across months — SIPs, subscriptions, rent-like transfers.
export default function RecurringPanel({ items = [] }) {
  const navigate = useNavigate()

  const active = items.filter(i => i.active)
  const stopped = items.filter(i => !i.active)
  const monthlyCommit = active
    .filter(i => i.cadence === 'monthly')
    .reduce((s, i) => s + i.amount, 0)

  const ordered = [...active, ...stopped]

  return (
    <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#2d3148]">
        <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] flex items-center gap-1.5">
          <RefreshCw size={12} /> Recurring & Subscriptions
        </p>
        {active.length > 0 && (
          <p className="text-xs text-[#94a3b8]">
            <Amount value={monthlyCommit} decimals={0} className="text-[#e2e8f0] font-semibold" />
            <span className="text-[#64748b]">/mo across {active.length}</span>
          </p>
        )}
      </div>

      {ordered.length === 0 ? (
        <p className="px-4 py-4 text-sm text-[#475569]">
          No recurring charges detected yet — they surface once a merchant bills you across a few months.
        </p>
      ) : (
        <table className="w-full text-sm">
          <tbody>
            {ordered.map(item => (
              <tr
                key={`${item.name}-${item.amount}`}
                onClick={() => navigate(txnFilterPath({ merchant: item.name }))}
                className={`border-b border-[#1a1d27] cursor-pointer hover:bg-[#1a1d27] transition-colors ${
                  item.active ? '' : 'opacity-50'
                }`}
              >
                <td className="px-4 py-2.5">
                  <span className="text-[#e2e8f0]">{item.name}</span>
                  {!item.active && (
                    <span className="ml-2 text-[10px] uppercase tracking-wider text-[#64748b] bg-[#1e2235] px-1.5 py-0.5 rounded">Stopped</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {item.category && (
                    <span className="bg-[#1e1b4b] text-[#a5b4fc] text-xs px-2 py-0.5 rounded-full whitespace-nowrap">
                      {item.category}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-xs text-[#64748b] whitespace-nowrap">
                  {item.cadence === 'monthly' ? 'Monthly' : `${item.months_seen}× seen`}
                </td>
                <td className="px-4 py-2.5 text-xs text-[#64748b] whitespace-nowrap">
                  last {dayjs(item.last_date).format('DD MMM')}
                </td>
                <td className="px-4 py-2.5 text-right text-[#cbd5e1] tabular-nums whitespace-nowrap">
                  <Amount value={item.amount} decimals={0} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
