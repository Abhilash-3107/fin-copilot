import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, LayoutDashboard, List, ClipboardCheck, Layers,
  Users, Upload, BarChart2, Settings as SettingsIcon, Receipt,
} from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { formatRupees } from './Amount.jsx'
import { txnFilterPath } from '../lib/txnLink.js'

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

// Global search over description / merchant / amount / person, plus jump-to-page.
// Opened with Cmd/Ctrl-K. Results are grouped; ↑/↓ move, Enter activates, Esc closes.
export default function CommandPalette() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [txns, setTxns] = useState([])
  const [people, setPeople] = useState([])
  const [groups, setGroups] = useState([])
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)

  const close = useCallback(() => {
    setOpen(false)
    setQuery('')
    setTxns([]); setPeople([]); setGroups([])
    setActive(0)
  }, [])

  // Global open shortcut.
  useEffect(() => {
    function onKey(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(o => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0)
  }, [open])

  // Debounced fetch across transactions, people and groups.
  useEffect(() => {
    const term = query.trim()
    if (!term) { setTxns([]); setPeople([]); setGroups([]); return }
    const t = setTimeout(async () => {
      try {
        const [tx, pp, gg] = await Promise.all([
          api.get(`/transactions?q=${encodeURIComponent(term)}&include=annotation&limit=6`),
          api.get(`/people?q=${encodeURIComponent(term)}`),
          api.get(`/groups?q=${encodeURIComponent(term)}`),
        ])
        setTxns(tx); setPeople(pp.slice(0, 5)); setGroups(gg.slice(0, 5))
        setActive(0)
      } catch (_) {
        // Palette is best-effort; a failed fetch just shows fewer results.
      }
    }, 200)
    return () => clearTimeout(t)
  }, [query])

  // Flatten every result into one ordered list so keyboard nav is uniform.
  const items = useMemo(() => {
    const term = query.trim().toLowerCase()
    const pageMatches = (term
      ? PAGES.filter(p => p.label.toLowerCase().includes(term))
      : PAGES
    ).map(p => ({ kind: 'page', key: `page:${p.to}`, icon: p.icon, label: p.label, run: () => navigate(p.to) }))

    const txnMatches = txns.map(t => ({
      kind: 'txn',
      key: `txn:${t.id}`,
      icon: Receipt,
      label: t.merchant || t.raw_description,
      meta: `${dayjs(t.txn_date).format('DD MMM')} · ${formatRupees(t.amount, { decimals: 0, debitCredit: t.debit_credit })}`,
      run: () => navigate(`/transactions?q=${encodeURIComponent(t.merchant || t.raw_description)}`),
    }))

    const peopleMatches = people.map(p => ({
      kind: 'person',
      key: `person:${p.id}`,
      icon: Users,
      label: p.name,
      meta: p.upi ? 'person' : 'person · no match set',
      run: () => p.upi
        ? navigate(txnFilterPath({ person: p.upi }), { state: { personName: p.name } })
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

    return [...pageMatches, ...txnMatches, ...peopleMatches, ...groupMatches]
  }, [query, txns, people, groups, navigate])

  const runItem = useCallback((item) => {
    if (!item) return
    item.run()
    close()
  }, [close])

  function onKeyDown(e) {
    if (e.key === 'Escape') { close(); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); runItem(items[active]) }
  }

  if (!open) return null

  const GROUP_TITLES = { page: 'Go to', txn: 'Transactions', person: 'People', group: 'Groups' }
  let lastKind = null

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[12vh] bg-black/50" onClick={close}>
      <div
        className="w-[36rem] max-w-[92vw] bg-[#13151f] border border-[#2d3148] rounded-xl shadow-2xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#2d3148]">
          <Search size={16} className="text-[#475569]" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search transactions, people, groups… or jump to a page"
            className="flex-1 bg-transparent outline-none text-sm text-[#e2e8f0] placeholder:text-[#475569]"
          />
          <kbd className="text-[10px] text-[#475569] border border-[#2d3148] rounded px-1.5 py-0.5">esc</kbd>
        </div>

        <div className="max-h-[52vh] overflow-y-auto py-1">
          {items.length === 0 ? (
            <p className="px-4 py-6 text-sm text-[#64748b] text-center">
              {query.trim() ? 'No matches.' : 'Type to search.'}
            </p>
          ) : (
            items.map((item, i) => {
              const showHeader = item.kind !== lastKind
              lastKind = item.kind
              const Icon = item.icon
              return (
                <div key={item.key}>
                  {showHeader && (
                    <p className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-[#475569]">
                      {GROUP_TITLES[item.kind]}
                    </p>
                  )}
                  <button
                    onClick={() => runItem(item)}
                    onMouseEnter={() => setActive(i)}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                      i === active ? 'bg-[#1e2440]' : 'hover:bg-[#1a1d27]'
                    }`}
                  >
                    <Icon size={15} className="text-[#64748b] shrink-0" />
                    <span className="text-sm text-[#e2e8f0] flex-1 truncate">{item.label}</span>
                    {item.meta && <span className="text-xs text-[#64748b] shrink-0 tabular-nums">{item.meta}</span>}
                  </button>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
