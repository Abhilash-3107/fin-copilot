import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'
import { RefreshCw } from 'lucide-react'
import Amount from './Amount.jsx'
import { txnFilterPath } from '../lib/txnLink.js'

function Row({ item, onOpen }) {
  return (
    <tr
      onClick={() => onOpen(item)}
      className={`border-b border-[#1a1d27] cursor-pointer hover:bg-[#1a1d27] transition-colors ${
        item.active ? '' : 'opacity-50'
      }`}
    >
      <td className="px-4 py-2.5">
        <span className="text-[#e2e8f0]">{item.name}</span>
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
  )
}

function Subheading({ children }) {
  return (
    <tr>
      <td colSpan={5} className="px-4 pt-3 pb-1 text-xs text-[#64748b] border-b border-[#1a1d27]">
        {children}
      </td>
    </tr>
  )
}

// The committed-money panel on Money Map. Fed by the server's recurrence
// detection (insights.recurring): charges of the same amount from the same
// counterparty across months - SIPs, subscriptions, rent-like transfers.
// The header is the server's recurring_summary and is exactly the active
// monthly rows listed first; irregular and stopped charges follow under their
// own headings and are not in it.
export default function RecurringPanel({ items = [], summary = { monthly_total: 0, count: 0 } }) {
  const navigate = useNavigate()
  // By the key the row was grouped on, never by its name: a name is only a label.
  const open = item => navigate(txnFilterPath({ payeeKeys: item.keys }), { state: { payeeName: item.name } })

  const monthly = items.filter(i => i.active && i.cadence === 'monthly')
  const irregular = items.filter(i => i.active && i.cadence !== 'monthly')
  const stopped = items.filter(i => !i.active)

  return (
    <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#2d3148]">
        <p className="text-xs font-semibold uppercase tracking-wider text-[#64748b] flex items-center gap-1.5">
          <RefreshCw size={12} /> Recurring & Subscriptions
        </p>
        {summary.count > 0 && (
          <p className="text-xs text-[#94a3b8]">
            <Amount value={summary.monthly_total} decimals={0} className="text-[#e2e8f0] font-semibold" />
            <span className="text-[#64748b]">/mo across {summary.count}</span>
          </p>
        )}
      </div>

      {items.length === 0 ? (
        <p className="px-4 py-4 text-sm text-[#475569]">
          No recurring charges detected yet - they surface once a merchant bills you across a few months.
        </p>
      ) : (
        <table className="w-full text-sm">
          <tbody>
            {monthly.map(item => <Row key={`${item.keys[0]}-${item.amount}`} item={item} onOpen={open} />)}
            {irregular.length > 0 && (
              <>
                <Subheading>Regular but not monthly - not in the monthly total</Subheading>
                {irregular.map(item => <Row key={`${item.keys[0]}-${item.amount}`} item={item} onOpen={open} />)}
              </>
            )}
            {stopped.length > 0 && (
              <>
                <Subheading>Stopped</Subheading>
                {stopped.map(item => <Row key={`${item.keys[0]}-${item.amount}`} item={item} onOpen={open} />)}
              </>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}
