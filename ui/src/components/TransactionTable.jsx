import dayjs from 'dayjs'
import { ChevronUp, ChevronDown } from 'lucide-react'
import Amount from './Amount.jsx'
import { SOURCE_PILL, sourceLabel } from '../lib/sources.js'

// Sortable header for the Date/Amount columns (4.12). Clicking toggles the
// sort key; clicking the active key flips direction.
function SortHeader({ label, col, sort, order, onSort, align = 'left' }) {
  const active = sort === col
  return (
    <button
      onClick={() => onSort(col)}
      className={`inline-flex items-center gap-1 uppercase tracking-wider ${active ? 'text-[#a5b4fc]' : 'hover:text-[#94a3b8]'} ${align === 'right' ? 'flex-row-reverse' : ''}`}
    >
      {label}
      {active && (order === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
    </button>
  )
}

export default function TransactionTable({
  transactions, annotationMap = {}, activeId, onSelect,
  selectable = false, selected, onToggle, onToggleAll, sort, order, onSort,
}) {
  if (!transactions.length) return null

  const allSelected = selectable && transactions.every(t => selected?.has(t.id))
  const th = 'sticky top-0 bg-[#13151f] px-3 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235] z-10 whitespace-nowrap'

  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr>
          {selectable && (
            <th className={`${th} w-8`}>
              <input
                type="checkbox"
                checked={allSelected}
                onChange={onToggleAll}
                className="accent-[#6366f1] align-middle"
                aria-label="Select all"
              />
            </th>
          )}
          <th className={`${th} text-left`}>
            {onSort ? <SortHeader label="Date" col="date" sort={sort} order={order} onSort={onSort} /> : 'Date'}
          </th>
          <th className={`${th} text-left`}>Description</th>
          <th className={`${th} text-right`}>
            {onSort ? <SortHeader label="Amount" col="amount" sort={sort} order={order} onSort={onSort} align="right" /> : 'Amount'}
          </th>
          {['Category', 'Source', 'Confidence'].map(h => (
            <th key={h} className={`${th} text-left`}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {transactions.map(txn => {
          const ann = annotationMap[txn.id]
          const isDebit = txn.debit_credit === 'debit'
          const src = ann?.source ?? 'pending'
          const isActive = txn.id === activeId
          const isSelected = selectable && selected?.has(txn.id)
          let upiNote = null
          try {
            const meta = typeof txn.upi_meta === 'string' ? JSON.parse(txn.upi_meta) : txn.upi_meta
            // Only show a note that adds information — bank descriptions often
            // already embed it ("UPI/Blinkit/…/Blinkit Payment"), and repeating
            // it as a second line is pure noise.
            if (
              meta?.note &&
              meta.note.length < 60 &&
              !txn.raw_description.toLowerCase().includes(meta.note.toLowerCase())
            ) upiNote = meta.note
          } catch (_) {}

          return (
            <tr
              key={txn.id}
              onClick={() => onSelect?.(txn)}
              className={`border-b border-[#1a1d27] cursor-pointer transition-colors duration-100 ${
                isActive
                  ? 'bg-[#1e2440] shadow-[inset_2px_0_0_#6366f1]' // inset accent, not a border: no 2px reflow
                  : isSelected
                    ? 'bg-[#171a2e]'
                    : 'hover:bg-[#1a1d27]'
              }`}
            >
              {selectable && (
                <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={!!isSelected}
                    onChange={() => onToggle(txn.id)}
                    className="accent-[#6366f1] align-middle"
                    aria-label="Select transaction"
                  />
                </td>
              )}
              <td className="px-3 py-2.5 whitespace-nowrap text-[#94a3b8]">
                {dayjs(txn.txn_date).format('DD MMM YY')}
              </td>
              <td className="px-3 py-2.5 text-[#cbd5e1] max-w-[480px]">
                <div className="truncate max-w-[460px]" title={txn.raw_description}>
                  {txn.raw_description}
                </div>
                {upiNote && (
                  <div className="text-xs text-[#818cf8] truncate max-w-[460px]">{upiNote}</div>
                )}
              </td>
              <td className={`px-3 py-2.5 whitespace-nowrap tabular-nums font-medium text-right ${isDebit ? 'text-red-400' : 'text-green-400'}`}>
                <Amount value={txn.amount} debitCredit={txn.debit_credit} />
              </td>
              <td className="px-3 py-2.5">
                {ann ? (
                  <span className="inline-flex items-center gap-1">
                    <span className="bg-[#1e1b4b] text-[#a5b4fc] text-xs px-2 py-0.5 rounded-full whitespace-nowrap">
                      {ann.subcategory ? `${ann.category} › ${ann.subcategory}` : ann.category}
                    </span>
                  </span>
                ) : (
                  <span className="text-[#475569]">—</span>
                )}
              </td>
              <td className="px-3 py-2.5">
                <span className={`inline-block text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap ${SOURCE_PILL[src] ?? SOURCE_PILL.pending}`}>
                  {sourceLabel(src)}
                </span>
              </td>
              <td className="px-3 py-2.5 whitespace-nowrap">
                {/* Manual annotations are human truth: a stale model confidence
                    next to a "manual" pill reads as a contradiction, so show
                    nothing. High confidence is the norm — plain muted text —
                    and color is reserved for the rows that need attention. */}
                {ann?.confidence == null || src === 'manual' ? (
                  <span className="text-[#475569]">—</span>
                ) : ann.confidence >= 0.8 ? (
                  <span className="text-xs tabular-nums text-[#64748b]">
                    {Math.round(ann.confidence * 100)}%
                  </span>
                ) : (
                  <span className={`inline-block text-xs px-2 py-0.5 rounded-full font-medium tabular-nums ${
                    ann.confidence >= 0.5 ? 'bg-[#78350f] text-[#fcd34d]' : 'bg-[#450a0a] text-[#fca5a5]'
                  }`}>
                    {Math.round(ann.confidence * 100)}%
                  </span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
