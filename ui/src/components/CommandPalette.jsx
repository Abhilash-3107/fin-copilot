import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, LayoutDashboard, List, ClipboardCheck, Layers,
  Users, Upload, BarChart2, Settings as SettingsIcon, Receipt,
} from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import Amount from './Amount.jsx'
import { txnFilterPath } from '../lib/txnLink.js'
import { useLedger } from '../contexts/LedgerContext.jsx'
import { useDialog } from '../lib/useDialog.js'
import { ALL_TIME } from '../contexts/PeriodContext.jsx'
import { OPEN_PALETTE_EVENT, PALETTE_KEY } from '../lib/shortcuts.js'

// Static destinations, always searchable by label.
const PAGES = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/transactions', icon: List, label: 'Transactions' },
  { to: '/review', icon: ClipboardCheck, label: 'Teach Me' },
  { to: '/groups', icon: Layers, label: 'Groups' },
  { to: '/people', icon: Users, label: 'People' },
  { to: '/upload', icon: Upload, label: 'Add Statement' },
  { to: '/insights', icon: BarChart2, label: 'Money Map' },
  { to: '/settings', icon: SettingsIcon, label: 'Settings' },
]

// Payments fetched per search. A few are listed; the rest only show that a payee repeats.
const FETCH_LIMIT = 20
const LISTED_PAYMENTS = 6

// A payee that shows up in two or more results becomes one result that filters
// to all of its payments, so "Swiggy" is not six identical rows. Needs the
// payee's key: a payment without one has nothing to filter by.
export function payeeSummaries(rows) {
  const byKey = new Map()
  for (const t of rows) {
    if (!t.counterparty_key) continue
    const entry = byKey.get(t.counterparty_key) ?? { key: t.counterparty_key, name: t.payee_all ?? t.payee, count: 0 }
    entry.count += 1
    byKey.set(t.counterparty_key, entry)
  }
  return [...byKey.values()].filter(e => e.count > 1)
}

// Global search over payments / people / groups, plus jump-to-page. Opened with
// Cmd/Ctrl-K or from the top bar's search field. Up/Down move, Enter opens, Esc closes.
export default function CommandPalette() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    function onKey(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === PALETTE_KEY) {
        e.preventDefault()
        setOpen(o => !o)
      }
    }
    const show = () => setOpen(true)
    window.addEventListener('keydown', onKey)
    window.addEventListener(OPEN_PALETTE_EVENT, show)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener(OPEN_PALETTE_EVENT, show)
    }
  }, [])

  return open ? <Palette onClose={() => setOpen(false)} /> : null
}

function Palette({ onClose }) {
  const navigate = useNavigate()
  const { multiple } = useLedger()
  const listId = useId()
  const [query, setQuery] = useState('')
  const [txns, setTxns] = useState([])
  const [people, setPeople] = useState([])
  const [groups, setGroups] = useState([])
  const [active, setActive] = useState(0)
  // A failed search says so quietly, with a retry; it is never "No matches".
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const dialog = useDialog({ onCancel: onClose })

  // Debounced fetch across payments, people and groups.
  useEffect(() => {
    const term = query.trim()
    if (!term) { setTxns([]); setPeople([]); setGroups([]); setFailed(false); return }
    let current = true
    const t = setTimeout(async () => {
      try {
        const [tx, pp, gg] = await Promise.all([
          api.get(`/transactions?q=${encodeURIComponent(term)}&include=annotation&limit=${FETCH_LIMIT}`),
          api.get(`/people?q=${encodeURIComponent(term)}`),
          api.get(`/groups?q=${encodeURIComponent(term)}`),
        ])
        if (!current) return
        setTxns(tx); setPeople(pp.slice(0, 5)); setGroups(gg.slice(0, 5))
        setFailed(false)
        setActive(0)
      } catch (_) {
        if (current) setFailed(true)
      }
    }, 200)
    return () => { current = false; clearTimeout(t) }
  }, [query, attempt])

  // Flatten every result into one ordered list so keyboard nav is uniform.
  const items = useMemo(() => {
    const term = query.trim().toLowerCase()
    const pageMatches = (term
      ? PAGES.filter(p => p.label.toLowerCase().includes(term))
      : PAGES
    ).map(p => ({ kind: 'page', key: `page:${p.to}`, icon: p.icon, label: p.label, run: () => navigate(p.to) }))

    const payeeMatches = payeeSummaries(txns).map(e => ({
      kind: 'payee',
      key: `payee:${e.key}`,
      icon: Receipt,
      label: `${e.name} - all payments`,
      meta: 'all months',
      run: () => navigate(
        txnFilterPath({ payeeKeys: [e.key], month: ALL_TIME }),
        { state: { payeeName: e.name } },
      ),
    }))

    // Each payment opens itself: the Transactions page switches to its month
    // and opens its panel, whatever the period was when the search started.
    const txnMatches = txns.slice(0, LISTED_PAYMENTS).map(t => ({
      kind: 'txn',
      key: `txn:${t.id}`,
      icon: Receipt,
      label: t.payee,
      meta: <>{dayjs(t.txn_date).format('DD MMM')} · <Amount value={t.amount} decimals={0} debitCredit={t.debit_credit} />{multiple && t.account_label ? ` · ${t.account_label}` : ''}</>,
      run: () => navigate(`/transactions?txn=${encodeURIComponent(t.id)}`),
    }))

    // A person filters the Ledger to their payments across every month (the
    // People page's "matched" link, over all time), never to a text search.
    const peopleMatches = people.map(p => ({
      kind: 'person',
      key: `person:${p.id}`,
      icon: Users,
      label: p.name,
      meta: p.upi ? 'person' : 'person · no match set',
      run: () => p.upi
        ? navigate(txnFilterPath({ person: p.upi, month: ALL_TIME }), { state: { personName: p.name } })
        : navigate('/people'),
    }))

    const groupMatches = groups.map(g => ({
      kind: 'group',
      key: `group:${g.id}`,
      icon: Layers,
      label: g.name,
      meta: 'group',
      run: () => navigate('/groups'),
    }))

    return [...pageMatches, ...payeeMatches, ...txnMatches, ...peopleMatches, ...groupMatches]
  }, [query, txns, people, groups, navigate])

  const runItem = useCallback((item) => {
    if (!item) return
    item.run()
    onClose()
  }, [onClose])

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); runItem(items[active]) }
  }

  const GROUP_TITLES = { page: 'Go to', payee: 'Payees', txn: 'Transactions', person: 'People', group: 'Groups' }
  const optionId = i => `${listId}-${i}`
  let lastKind = null

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[12vh] bg-black/50" onClick={onClose}>
      <div
        {...dialog}
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        className="w-[36rem] max-w-[92vw] bg-[#13151f] border border-[#2d3148] rounded-xl shadow-2xl overflow-hidden outline-none"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#2d3148]">
          <Search size={16} className="text-[#94a3b8]" />
          <input
            data-autofocus
            value={query}
            onChange={e => { setQuery(e.target.value); setActive(0) }}
            onKeyDown={onKeyDown}
            placeholder="Search payments, people, groups… or jump to a page"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={items.length ? optionId(Math.min(active, items.length - 1)) : undefined}
            aria-label="Search"
            className="flex-1 bg-transparent outline-none text-sm text-[#e2e8f0] placeholder:text-[#94a3b8]"
          />
          <kbd className="text-[10px] text-[#94a3b8] border border-[#2d3148] rounded px-1.5 py-0.5">esc</kbd>
        </div>

        <div id={listId} role="listbox" aria-label="Results" className="max-h-[52vh] overflow-y-auto py-1">
          {failed && (
            <p className="flex items-center justify-center gap-2 px-4 py-3 text-xs text-[#fca5a5]" role="status">
              Couldn't search just now.
              <button
                type="button"
                onClick={() => setAttempt(n => n + 1)}
                className="underline underline-offset-2 hover:text-white"
              >
                Retry
              </button>
            </p>
          )}
          {items.length === 0 ? (
            !failed && (
              <p className="px-4 py-6 text-sm text-[#94a3b8] text-center">
                {query.trim() ? 'No matches.' : 'Type to search.'}
              </p>
            )
          ) : (
            items.map((item, i) => {
              const showHeader = item.kind !== lastKind
              lastKind = item.kind
              const Icon = item.icon
              return (
                <div key={item.key} role="presentation">
                  {showHeader && (
                    <p role="presentation" className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8]">
                      {GROUP_TITLES[item.kind]}
                    </p>
                  )}
                  <div
                    id={optionId(i)}
                    role="option"
                    aria-selected={i === active}
                    onClick={() => runItem(item)}
                    onMouseEnter={() => setActive(i)}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-left cursor-pointer transition-colors ${
                      i === active ? 'bg-[#1e2440]' : 'hover:bg-[#1a1d27]'
                    }`}
                  >
                    <Icon size={15} className="text-[#94a3b8] shrink-0" />
                    <span className="text-sm text-[#e2e8f0] flex-1 truncate">{item.label}</span>
                    {item.meta && <span className="text-xs text-[#94a3b8] shrink-0 tabular-nums">{item.meta}</span>}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
