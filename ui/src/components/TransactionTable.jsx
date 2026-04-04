import dayjs from 'dayjs'

export const SOURCE_PILL = {
  manual:       'bg-[#14532d] text-[#86efac]',
  rule:         'bg-[#1e3a5f] text-[#7dd3fc]',
  rag_direct:   'bg-[#164e63] text-[#67e8f9]',
  rag_prompted: 'bg-[#164e63] text-[#67e8f9]',
  llm:          'bg-[#3b1f5e] text-[#c4b5fd]',
  model:        'bg-[#3b1f5e] text-[#c4b5fd]',
  imported:     'bg-[#292524] text-[#d6d3d1]',
  pending:      'bg-[#292524] text-[#a8a29e]',
}

function sourceLabel(src) {
  if (src === 'rag_direct' || src === 'rag_prompted') return 'from history'
  return src ?? 'pending'
}

export function formatAmount(amount, debitCredit) {
  const formatted = Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2 })
  return debitCredit === 'debit' ? `−₹${formatted}` : `+₹${formatted}`
}

export default function TransactionTable({ transactions, annotationMap = {}, activeId, onSelect }) {
  if (!transactions.length) return null

  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr>
          {['Date', 'Description', 'Amount', 'Category', 'Source', 'Confidence'].map(h => (
            <th
              key={h}
              className="sticky top-0 bg-[#13151f] px-3 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235] z-10 whitespace-nowrap"
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {transactions.map(txn => {
          const ann = annotationMap[txn.id]
          const isDebit = txn.debit_credit === 'debit'
          const src = ann?.source ?? 'pending'
          const isActive = txn.id === activeId
          let upiNote = null
          try {
            const meta = typeof txn.upi_meta === 'string' ? JSON.parse(txn.upi_meta) : txn.upi_meta
            if (meta?.note && meta.note.length < 60) upiNote = meta.note
          } catch (_) {}

          return (
            <tr
              key={txn.id}
              onClick={() => onSelect?.(txn)}
              className={`border-b border-[#1a1d27] cursor-pointer transition-colors duration-100 ${
                isActive
                  ? 'bg-[#1e2440] border-l-2 border-l-[#6366f1]'
                  : 'hover:bg-[#1a1d27]'
              }`}
            >
              <td className="px-3 py-2.5 whitespace-nowrap text-[#94a3b8]">
                {dayjs(txn.txn_date).format('DD MMM YY')}
              </td>
              <td className="px-3 py-2.5 text-[#cbd5e1] max-w-xs">
                <div className="truncate max-w-[320px]" title={txn.raw_description}>
                  {txn.raw_description}
                </div>
                {upiNote && (
                  <div className="text-xs text-[#818cf8] truncate max-w-[320px]">{upiNote}</div>
                )}
              </td>
              <td className={`px-3 py-2.5 whitespace-nowrap tabular-nums font-medium ${isDebit ? 'text-red-400' : 'text-green-400'}`}>
                {formatAmount(txn.amount, txn.debit_credit)}
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
                {ann?.confidence != null ? (
                  <span className={`inline-block text-xs px-2 py-0.5 rounded-full font-medium tabular-nums ${
                    ann.confidence >= 0.8 ? 'bg-[#14532d] text-[#86efac]'
                    : ann.confidence >= 0.5 ? 'bg-[#78350f] text-[#fcd34d]'
                    : 'bg-[#450a0a] text-[#fca5a5]'
                  }`}>
                    {Math.round(ann.confidence * 100)}%
                  </span>
                ) : (
                  <span className="text-[#475569]">—</span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
