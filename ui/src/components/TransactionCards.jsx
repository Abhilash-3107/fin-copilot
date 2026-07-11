import dayjs from 'dayjs'
import Amount from './Amount.jsx'
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
        const isDebit = txn.debit_credit === 'debit'
        const src = ann?.source ?? 'pending'
        const isActive = txn.id === activeId
        const isSelected = selectable && selected?.has(txn.id)

        return (
          <li
            key={txn.id}
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
                aria-label="Select transaction"
              />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <span className="text-sm text-[#cbd5e1] truncate">{txn.raw_description}</span>
                <span className={`text-sm font-medium tabular-nums whitespace-nowrap ${isDebit ? 'text-red-400' : 'text-green-400'}`}>
                  <Amount value={txn.amount} debitCredit={txn.debit_credit} />
                </span>
              </div>
              <div className="mt-1.5 flex items-center gap-2 flex-wrap">
                <span className="text-xs text-[#64748b]">{dayjs(txn.txn_date).format('DD MMM YY')}</span>
                {ann && (
                  <span className="bg-[#1e1b4b] text-[#a5b4fc] text-xs px-2 py-0.5 rounded-full">
                    {ann.subcategory ? `${ann.category} › ${ann.subcategory}` : ann.category}
                  </span>
                )}
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
