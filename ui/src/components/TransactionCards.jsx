import dayjs from 'dayjs'
import Amount from './Amount.jsx'
import PaymentLabel from './PaymentLabel.jsx'
import CategoryPill from './CategoryPill.jsx'
import { SOURCE_PILL, sourceLabel } from '../lib/sources.js'

// Card-per-row rendering of transactions for narrow screens (4.1). The wide
// table is unreadable below md; each card stacks the same fields vertically.
// Selection checkbox is included so bulk actions work on mobile too.
export default function TransactionCards({
  transactions, annotationMap = {}, activeId, onSelect,
  selectable = false, selected, onToggle,
}) {
  if (!transactions.length) return null

  return (
    <ul className="divide-y divide-[#1a1d27]">
      {transactions.map(txn => {
        const ann = annotationMap[txn.id]
        const src = ann?.source ?? 'pending'
        const isActive = txn.id === activeId
        const isSelected = selectable && selected?.has(txn.id)

        return (
          <li
            key={txn.id}
            data-txn-id={txn.id}
            onClick={() => onSelect?.(txn)}
            className={`flex gap-3 px-4 py-3 cursor-pointer transition-colors ${
              isActive ? 'bg-[#1e2440]' : isSelected ? 'bg-[#171a2e]' : 'active:bg-[#1a1d27]'
            }`}
          >
            {selectable && (
              <input
                type="checkbox"
                checked={!!isSelected}
                onClick={e => e.stopPropagation()}
                onChange={() => onToggle(txn.id)}
                className="accent-[#6366f1] mt-1 shrink-0"
                aria-label="Select payment"
              />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <PaymentLabel txn={txn} className="text-sm" />
                <span className="text-sm font-medium tabular-nums whitespace-nowrap">
                  <Amount value={txn.amount} debitCredit={txn.debit_credit} />
                </span>
              </div>
              <div className="mt-1.5 flex items-center gap-2 flex-wrap">
                <span className="text-xs text-[#94a3b8]">{dayjs(txn.txn_date).format('DD MMM YY')}</span>
                {ann && <CategoryPill txn={txn} ann={ann} />}
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${SOURCE_PILL[src] ?? SOURCE_PILL.pending}`}>
                  {sourceLabel(src)}
                </span>
              </div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
