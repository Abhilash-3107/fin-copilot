import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pencil } from 'lucide-react'
import dayjs from 'dayjs'
import Amount from './Amount.jsx'
import NameField from './NameField.jsx'
import { count } from '../lib/plural.js'
import { txnFilterPath } from '../lib/txnLink.js'

// How much of a month a cell's statements cover, in words: "Full month", "to 15 Nov",
// "from 10 Oct", "10 to 15 Nov"; two statements say so.
export function coverageText(cell, month) {
  const first = `${month}-01`
  const last = dayjs(first).endOf('month').format('YYYY-MM-DD')
  const day = d => dayjs(d).format('D MMM')
  const parts = cell.full ? ['Full month'] : cell.covered.map(([start, end]) => {
    if (start === first) return `to ${day(end)}`
    if (end === last) return `from ${day(start)}`
    return `${dayjs(start).format('D')} to ${day(end)}`
  })
  const several = cell.statement_ids.length > 1 ? [`${cell.statement_ids.length} statements`] : []
  return [...parts, ...several].join(', ')
}

// The stored balance check of the cell's statements, in words.
export function checkText({ checked, consistent }) {
  if (!checked) return 'No balances to check'
  return `${consistent} of ${checked} balances check out`
}

function accessibleName(label, month, cell) {
  const when = dayjs(month).format('MMMM YYYY')
  if (cell.gap) return `${label}, ${when}, no statement`
  const { checked, consistent } = cell.balance_check
  const state = !checked ? 'no balances to check' : consistent === checked ? 'balanced' : 'not fully balanced'
  return `${label}, ${when}, ${count(cell.payments, 'payment')}, ${state}`
}

function Cell({ account, month, cell, selected, onShowStatements }) {
  const when = dayjs(month).format('MMMM YYYY')
  if (!cell) return <td className="border-b border-[#1a1d27]" />
  if (cell.gap) {
    return (
      <td className="px-2 py-2 border-b border-[#1a1d27] align-top">
        <div
          role="img"
          aria-label={accessibleName(account.account_label, month, cell)}
          className="rounded-md border border-dashed border-[#78350f] px-2 py-1.5 text-xs text-[#fdba74]"
        >
          No statement
        </div>
      </td>
    )
  }
  const { checked, consistent } = cell.balance_check
  const off = checked > 0 && consistent < checked
  return (
    <td className="px-2 py-2 border-b border-[#1a1d27] align-top">
      <div className={`rounded-md border px-2 py-1.5 text-xs leading-snug ${selected ? 'border-[#6366f1] bg-[#1e2440]' : 'border-[#2d3148] bg-[#171a2e]'}`}>
        <Link
          to={txnFilterPath({ month, account: account.id })}
          aria-label={accessibleName(account.account_label, month, cell)}
          className="block rounded hover:text-[#a5b4fc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#6366f1]"
        >
          <span className="block font-medium text-[#e2e8f0]">{count(cell.payments, 'payment')}</span>
          <span className={`block ${cell.full ? 'text-[#94a3b8]' : 'text-[#fbbf24]'}`}>{coverageText(cell, month)}</span>
          <span className={`block ${off ? 'text-[#fdba74]' : 'text-[#94a3b8]'}`}>{checkText(cell.balance_check)}</span>
        </Link>
        <button
          type="button"
          aria-label={`Statements in ${account.account_label}, ${when}`}
          aria-pressed={selected}
          onClick={() => onShowStatements(account.id, month)}
          className="mt-1 text-[11px] text-[#818cf8] hover:underline"
        >
          {count(cell.statement_ids.length, 'statement')}
        </button>
      </div>
    </td>
  )
}

// The books: one row per account, one column per month, newest first. A month an
// account has no statement for, between its first and its last, is a visible gap.
// A cell opens Transactions for that account and month; its statements button picks
// them out in the list below, where every per-statement action lives.
export default function BooksGrid({ books, selected, onShowStatements, onRename }) {
  const [renaming, setRenaming] = useState(null)
  const renameButtons = useRef({})
  const months = [...books.months].reverse()
  if (books.accounts.length === 0) return null

  function endRename(id) {
    setRenaming(null)
    // Back to the control that started it.
    setTimeout(() => renameButtons.current[id]?.focus(), 0)
  }

  return (
    <section aria-labelledby="books-title" className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-[#2d3148]">
        <h2 id="books-title" className="text-xs font-semibold uppercase tracking-wider text-[#94a3b8]">Your books</h2>
        <p className="mt-1 text-xs text-[#94a3b8]">
          One row per account, one column per month. A month with no statement shows as a gap.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 z-10 bg-[#13151f] px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] border-b border-[#1e2235] min-w-[13rem]">
                Account
              </th>
              {months.map(m => (
                <th key={m} scope="col" className="px-2 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] border-b border-[#1e2235] min-w-[8.5rem] whitespace-nowrap">
                  {dayjs(m).format('MMM YYYY')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {books.accounts.map(account => (
              <tr key={account.id}>
                <th scope="row" className="sticky left-0 z-10 bg-[#13151f] px-4 py-2 text-left font-normal border-b border-[#1a1d27] align-top">
                  {renaming === account.id ? (
                    <NameField
                      initial={account.account_label}
                      onSave={async name => {
                        // The words already shown are no change, and must not turn a derived label into a name.
                        if (name.trim() === account.account_label || await onRename(account.id, name)) endRename(account.id)
                      }}
                      onCancel={() => endRename(account.id)}
                    />
                  ) : (
                    <>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[#e2e8f0] font-medium">{account.account_label}</span>
                        <button
                          type="button"
                          ref={node => { renameButtons.current[account.id] = node }}
                          onClick={() => setRenaming(account.id)}
                          aria-label={`Rename ${account.account_label}`}
                          className="text-[#94a3b8] hover:text-[#e2e8f0]"
                        >
                          <Pencil size={12} />
                        </button>
                      </div>
                      {account.balance != null && (
                        <p className="text-xs text-[#94a3b8]">
                          <Amount value={account.balance} decimals={0} className="text-[#e2e8f0]" />
                          {' '}as of {dayjs(account.balance_as_of).format('D MMM YYYY')}
                        </p>
                      )}
                    </>
                  )}
                </th>
                {months.map(m => (
                  <Cell
                    key={m}
                    account={account}
                    month={m}
                    cell={account.cells[m]}
                    selected={selected?.accountId === account.id && selected?.month === m}
                    onShowStatements={onShowStatements}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {books.transfers.length > 0 && (
        <div className="border-t border-[#2d3148] px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-[#94a3b8] mb-2">Moved between your accounts</p>
          <ul className="space-y-1 text-sm text-[#94a3b8]">
            {books.transfers.slice(0, 6).map(t => (
              <li key={t.transaction_id}>
                Moved <Amount value={t.amount} decimals={0} className="text-[#e2e8f0]" /> from {t.from_label} to {t.to_label} on {dayjs(t.txn_date).format('D MMM')}, not counted
              </li>
            ))}
          </ul>
          {books.transfers.length > 6 && <p className="mt-1 text-xs text-[#94a3b8]">and {books.transfers.length - 6} more</p>}
        </div>
      )}
    </section>
  )
}
