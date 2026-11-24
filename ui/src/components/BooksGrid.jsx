import { useEffect, useRef, useState } from 'react'
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

// The stored balance check of one statement, in words: "41 of 41 balances check out".
export function checkText({ checked, consistent }) {
  if (!checked) return 'No balances to check'
  return `${consistent} of ${checked} ${checked === 1 ? 'balance checks' : 'balances check'} out`
}

// Which statement a check is of, for a cell that holds several or whose statement spans
// several months: its months ("Aug to Oct"), the whole month ("Oct 2026"), or the days of
// the month it covers ("14 to 31 Oct").
function statementName({ months, covered }, month) {
  if (months) {
    const [first, last] = months.map(m => dayjs(m))
    const format = first.year() === last.year() ? 'MMM' : 'MMM YYYY'
    return `${first.format(format)} to ${last.format(format)}`
  }
  const [start, end] = covered
  const last = dayjs(`${month}-01`).endOf('month').format('YYYY-MM-DD')
  if (start === `${month}-01` && end === last) return dayjs(`${month}-01`).format('MMM YYYY')
  return `${dayjs(start).format('D')} to ${dayjs(end).format('D MMM')}`
}

// The check of everything a cell holds, one statement at a time and never added up: a
// statement's check is of its whole file, so summing the files of a cell (or reading a
// spanning file's count as this month's) would not say anything about the payments shown.
// A cell with one statement within the month needs no name for it.
export function cellCheckText(cell, month) {
  const checks = cell.statement_checks
  if (!checks) return checkText(cell.balance_check)
  if (checks.length === 1 && !checks[0].months) return checkText(checks[0])
  return checks.map(c => `${statementName(c, month)} statement: ${checkText(c)}`).join('; ')
}

// What a cell says in its small print: how much of the month is covered in a word or two, and
// the balances in a few characters. The full words are the cell's title and accessible name.
export function coverageShort(cell, month) {
  if (cell.full) return 'Full'
  if (cell.covered.length > 1) return 'Partial'
  const [start, end] = cell.covered[0]
  const first = `${month}-01`
  const last = dayjs(first).endOf('month').format('YYYY-MM-DD')
  const day = d => dayjs(d).format('D MMM')
  if (start === first) return `to ${day(end)}`
  if (end === last) return `from ${day(start)}`
  return `${dayjs(start).format('D')}-${day(end)}`
}

// The balances in a few characters: a word for the cell as a whole. The counts are in its
// title and accessible name, statement by statement.
export function checkShort({ checked, consistent }) {
  if (!checked) return 'No balances'
  return consistent === checked ? 'Balanced' : 'Not balanced'
}

// The whole sentence of a cell, for its accessible name and title.
function cellWords(label, month, cell) {
  const when = dayjs(month).format('MMMM YYYY')
  if (cell.gap) return `${label}, ${when}, no statement`
  return [
    label, when, count(cell.payments, 'payment'), coverageText(cell, month),
    cellCheckText(cell, month),
  ].join(', ')
}

function Cell({ account, month, cell, selected, onShowStatements }) {
  const when = dayjs(month).format('MMMM YYYY')
  if (!cell) return <td className="border-b border-[#1a1d27]" />
  if (cell.gap) {
    const words = cellWords(account.account_label, month, cell)
    return (
      <td className="px-1 py-2 border-b border-[#1a1d27] align-top">
        <div
          role="img"
          aria-label={words}
          title={words}
          className="h-[4.25rem] flex items-center justify-center rounded-md border border-dashed border-[#78350f] px-1 text-center text-[11px] leading-tight text-[#fdba74]"
        >
          No statement
        </div>
      </td>
    )
  }
  const { checked, consistent } = cell.balance_check
  const off = checked > 0 && consistent < checked
  const words = cellWords(account.account_label, month, cell)
  return (
    <td className="px-1 py-2 border-b border-[#1a1d27] align-top">
      <div className={`h-[4.25rem] rounded-md border px-1 py-1 text-[11px] leading-tight ${selected ? 'border-[#6366f1] bg-[#1e2440]' : 'border-[#2d3148] bg-[#171a2e]'}`}>
        <Link
          to={txnFilterPath({ month, account: account.id })}
          aria-label={words}
          title={words}
          className="block rounded whitespace-nowrap hover:text-[#a5b4fc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#6366f1]"
        >
          <span className="block font-medium text-[#e2e8f0]">{count(cell.payments, 'payment')}</span>
          <span className={`block ${cell.full ? 'text-[#94a3b8]' : 'text-[#fbbf24]'}`}>{coverageShort(cell, month)}</span>
          <span className={`block text-[10px] ${off ? 'text-[#fdba74]' : 'text-[#94a3b8]'}`}>{checkShort(cell.balance_check)}</span>
        </Link>
        <button
          type="button"
          aria-label={`Statements in ${account.account_label}, ${when}`}
          aria-pressed={selected}
          onClick={() => onShowStatements(account.id, month)}
          className="text-[#818cf8] hover:underline whitespace-nowrap"
        >
          {count(cell.statement_ids.length, 'statement')}
        </button>
      </div>
    </td>
  )
}

// The widths that fit 3 accounts by 12 months at 1440: below them the grid scrolls inside its box.
const ACCOUNT_REM = 10
const MONTH_REM = 6
const MONTH_MAX_REM = 11

// The books: one row per account, one column per month, newest first. A month an
// account has no statement for, between its first and its last, is a visible gap.
// A cell opens Transactions for that account and month; its statements button picks
// them out in the list below, where every per-statement action lives.
export default function BooksGrid({ books, selected, onShowStatements, onRename }) {
  const [renaming, setRenaming] = useState(null)
  const renameButtons = useRef({})
  const scroller = useRef(null)
  // Below the width that fits the months, the grid scrolls inside its box; the edge says there is more.
  const [moreRight, setMoreRight] = useState(false)
  const months = [...books.months].reverse()
  const shown = books.accounts.length > 0

  useEffect(() => {
    const box = scroller.current
    if (!box) return undefined
    const update = () => setMoreRight(box.scrollLeft + box.clientWidth < box.scrollWidth - 1)
    update()
    box.addEventListener('scroll', update, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    observer?.observe(box)
    return () => {
      box.removeEventListener('scroll', update)
      observer?.disconnect()
    }
  }, [shown, months.length])

  if (!shown) return null

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
      <div className="relative">
      <div ref={scroller} className="overflow-x-auto">
        {/* Fills the width when there are many months, but a month never grows wider than
            MONTH_MAX_REM, so one or two months still read as cells and not as banners. */}
        <table
          className="table-fixed border-collapse text-sm"
          style={{
            minWidth: `${ACCOUNT_REM + months.length * MONTH_REM}rem`,
            width: `min(100%, ${ACCOUNT_REM + months.length * MONTH_MAX_REM}rem)`,
          }}
        >
          <colgroup>
            <col style={{ width: `${ACCOUNT_REM}rem` }} />
            {months.map(m => <col key={m} />)}
          </colgroup>
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 z-10 bg-[#13151f] px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] border-b border-[#1e2235] ">
                Account
              </th>
              {months.map(m => (
                <th key={m} scope="col" className="px-1 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] border-b border-[#1e2235] whitespace-nowrap">
                  {dayjs(m).format('MMM YYYY')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {books.accounts.map(account => (
              <tr key={account.id}>
                <th scope="row" className="sticky left-0 z-10 bg-[#13151f] px-3 py-2 text-left font-normal border-b border-[#1a1d27] align-top">
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
                        <span className="min-w-0 truncate text-[#e2e8f0] font-medium" title={account.account_label}>{account.account_label}</span>
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
      {moreRight && (
        <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-[#13151f] to-transparent" />
      )}
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
