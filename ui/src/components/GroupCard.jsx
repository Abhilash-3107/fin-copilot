import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Trash2, HelpCircle, Plus, Search, X } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { formatDateRange } from '../lib/dates.js'
import { useToast } from '../contexts/ToastContext.jsx'
import Amount from './Amount.jsx'
import PeoplePicker from './PeoplePicker.jsx'
import Tooltip from './Tooltip.jsx'

const TXN_TYPES = ['split', 'reimbursement', 'refund', 'transfer', 'event']
const TYPE_HELP = 'Matters for payments you made. split / event / blank = shared cost, divided with you · transfer / refund = money you sent them, owed back in full · reimbursement = repaying something they covered, no effect. Money you received always counts as paid back.'

// Small inline searchbox that finds transactions not yet in this group and adds
// them on click. Scoped to the expanded group so "add transactions" stays where
// the group's members already live.
function AddTransactionSearch({ existingIds, onAdd }) {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    const t = setTimeout(async () => {
      try {
        const rows = await api.get(`/transactions?q=${encodeURIComponent(query.trim())}&limit=20`)
        setResults(rows.filter(r => !existingIds.has(r.id)))
        setOpen(true)
      } catch (e) {
        toast(`Search failed: ${e.message}`, 'error')
      }
    }, 300)
    return () => clearTimeout(t)
  }, [query, existingIds, toast])

  return (
    <div ref={ref} className="relative px-3 py-2.5">
      <div className="flex items-center gap-2 bg-[#0f1117] border border-[#2d3148] rounded-md px-2.5 py-1.5 focus-within:border-[#6366f1] transition-colors max-w-md">
        <Search size={13} className="text-[#475569] shrink-0" />
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onFocus={() => query.trim() && setOpen(true)}
          placeholder="Add a transaction to this group…"
          className="bg-transparent border-none outline-none text-xs text-[#e2e8f0] flex-1 placeholder:text-[#475569]"
        />
        {query && (
          <button type="button" onClick={() => { setQuery(''); setResults([]) }} className="text-[#475569] hover:text-[#94a3b8]">
            <X size={12} />
          </button>
        )}
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-30 left-3 right-3 mt-1 max-w-md bg-[#1a1d27] border border-[#2d3148] rounded-md shadow-xl max-h-64 overflow-auto">
          {results.map(r => (
            <button
              key={r.id}
              type="button"
              onClick={async () => { await onAdd(r.id); setQuery(''); setResults([]); setOpen(false) }}
              className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[#1e2440] transition-colors border-b border-[#1e2235] last:border-b-0"
            >
              <span className="text-[10px] text-[#64748b] whitespace-nowrap tabular-nums">{dayjs(r.txn_date).format('DD MMM YY')}</span>
              <span className="text-xs text-[#cbd5e1] flex-1 truncate">{r.raw_description}</span>
              <Amount value={r.amount} debitCredit={r.debit_credit} className={`text-xs tabular-nums shrink-0 ${r.debit_credit === 'debit' ? 'text-red-400' : 'text-green-400'}`} />
              <Plus size={12} className="text-[#6366f1] shrink-0" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function TypeSelect({ member, onChange }) {
  return (
    <select
      value={member.txn_type ?? ''}
      onChange={e => onChange({ txn_type: e.target.value })}
      aria-label={`Type for ${member.raw_description}`}
      className="bg-[#1a1d27] border border-[#2d3148] text-[#e2e8f0] px-1.5 py-0.5 rounded text-xs focus:outline-none focus:border-[#6366f1]"
    >
      <option value="">—</option>
      {TXN_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
    </select>
  )
}

function RemoveMemberButton({ member, onRemove }) {
  return (
    <button
      type="button"
      onClick={onRemove}
      aria-label={`Remove ${member.raw_description} from group`}
      className="text-[#475569] hover:text-red-400 transition-colors"
    >
      <Trash2 size={12} />
    </button>
  )
}

function SettleUp({ settleUp, hasPeople }) {
  if (!settleUp?.length) {
    // Tagged people with every balance at zero is an answer worth showing;
    // only a group with nobody tagged has nothing to settle.
    if (!hasPeople) return null
    return (
      <div className="border-t border-[#2d3148] px-4 py-3 bg-[#10121a] rounded-b-lg flex items-center gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b]">Settle up</span>
        <span className="text-xs text-green-400">Everyone's settled up</span>
      </div>
    )
  }
  return (
    <div className="border-t border-[#2d3148] px-4 py-3 bg-[#10121a] rounded-b-lg">
      <div className="flex items-center gap-1.5 mb-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b]">Settle up</span>
        <Tooltip content="Money a tagged person sent you counts in full. A payment you made is split evenly across them plus you (split, event or blank), counted in full (transfer, refund), or ignored (reimbursement: repaying something they covered)." position="bottom">
          <HelpCircle size={11} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
        </Tooltip>
      </div>
      <div className="flex flex-wrap gap-2">
        {settleUp.map(s => {
          const owesYou = s.net > 0
          return (
            <div key={s.person} className="flex items-center gap-2 bg-[#13151f] border border-[#2d3148] rounded-md px-2.5 py-1">
              <span className="text-xs text-[#cbd5e1]">{s.name ?? s.person}</span>
              <span className={`text-xs tabular-nums ${owesYou ? 'text-green-400' : 'text-red-400'}`}>
                {owesYou ? 'owes you ' : 'you owe '}<Amount value={Math.abs(s.net)} decimals={Number.isInteger(s.net) ? 0 : 2} />
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function GroupCard({ group, onDelete }) {
  const toast = useToast()
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [people, setPeople] = useState([])

  const summary = group.summary ?? {}

  async function reload() {
    const [data, allPeople] = await Promise.all([
      api.get(`/groups/${group.id}`),
      api.get('/people'),
    ])
    setDetail(data)
    setPeople(allPeople)
    return data
  }

  async function createPerson(name) {
    try {
      const person = await api.post('/people', { name })
      setPeople(prev => [...prev, person])
      return person
    } catch (e) {
      toast(`Couldn't add ${name}: ${e.message}`, 'error')
      return null
    }
  }

  async function toggle() {
    if (!expanded && !detail) {
      setLoading(true)
      try {
        await reload()
      } catch (e) {
        toast(`Failed to load group: ${e.message}`, 'error')
      } finally {
        setLoading(false)
      }
    }
    setExpanded(o => !o)
  }

  async function updateMember(txnId, patch) {
    try {
      await api.patch(`/groups/${group.id}/members/${txnId}`, patch)
      await reload()
    } catch (e) {
      toast(`Update failed: ${e.message}`, 'error')
    }
  }

  async function removeMember(txnId) {
    try {
      await api.delete(`/groups/${group.id}/members/${txnId}`)
      await reload()
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    }
  }

  async function addMember(txnId) {
    try {
      await api.post(`/groups/${group.id}/members`, { transaction_id: txnId })
      await reload()
      toast('Added to group', 'success')
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    }
  }

  const labels = Array.isArray(group.labels)
    ? group.labels
    : (group.labels ? group.labels.split(',').filter(Boolean) : [])

  const existingIds = new Set((detail?.members ?? []).map(m => m.transaction_id))
  const dateRange = formatDateRange(summary.first_date, summary.last_date)

  return (
    <div className="group bg-[#13151f] border border-[#2d3148] rounded-lg">
      {/* Header / collapsed summary */}
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} ${group.name}`}
          className="text-[#64748b] hover:text-[#94a3b8] shrink-0"
        >
          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
        <div className="flex-1 min-w-0 cursor-pointer" onClick={toggle}>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-[#e2e8f0]">{group.name}</span>
            {labels.map(l => (
              <span key={l} className="text-[10px] bg-[#1e2440] text-[#a5b4fc] px-1.5 py-0.5 rounded-full">{l}</span>
            ))}
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-xs text-[#64748b] flex-wrap">
            {dateRange && <span>{dateRange}</span>}
            {dateRange && <span className="text-[#2d3148]">·</span>}
            <span>{summary.member_count ?? 0} {summary.member_count === 1 ? 'txn' : 'txns'}</span>
            {group.note && <><span className="text-[#2d3148]">·</span><span className="truncate max-w-[240px]">{group.note}</span></>}
          </div>
        </div>
        {/* Net position */}
        {summary.member_count > 0 && (
          <div className="text-right shrink-0">
            <div className={`text-sm font-medium tabular-nums ${summary.net > 0 ? 'text-green-400' : summary.net < 0 ? 'text-red-400' : 'text-[#94a3b8]'}`}>
              {summary.net > 0 ? '+' : ''}<Amount value={summary.net} decimals={0} />
            </div>
            <div className="text-[10px] text-[#475569]">net</div>
          </div>
        )}
        <button
          type="button"
          onClick={() => onDelete?.(group)}
          className="text-[#475569] hover:text-red-400 transition-colors shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100"
          title="Delete group"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-[#2d3148]">
          {loading ? (
            <p className="px-4 py-3 text-xs text-[#475569]">Loading…</p>
          ) : (
            <>
              {detail?.members?.length ? (
                <>
                <table className="w-full text-xs hidden md:table">
                  <thead>
                    <tr>
                      {['Date', 'Description', 'Amount', 'People'].map(h => (
                        <th key={h} className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">{h}</th>
                      ))}
                      <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">
                        <span className="flex items-center gap-1">
                          Type
                          <Tooltip content={TYPE_HELP} position="bottom">
                            <HelpCircle size={11} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
                          </Tooltip>
                        </span>
                      </th>
                      <th className="px-3 py-2 border-b border-[#1e2235]"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.members.map(m => (
                      <tr key={m.transaction_id} className="border-b border-[#1a1d27] hover:bg-[#1a1d27]">
                        <td className="px-3 py-2 text-[#94a3b8] whitespace-nowrap">{dayjs(m.txn_date).format('DD MMM YY')}</td>
                        <td className="px-3 py-2 text-[#cbd5e1] max-w-[160px] truncate">{m.raw_description}</td>
                        <td className={`px-3 py-2 tabular-nums whitespace-nowrap ${m.debit_credit === 'debit' ? 'text-red-400' : 'text-green-400'}`}>
                          <Amount value={m.amount} debitCredit={m.debit_credit} />
                        </td>
                        <td className="px-3 py-1.5 min-w-[180px] w-[280px]">
                          <PeoplePicker
                            compact
                            selectedIds={m.people ?? []}
                            allPeople={people}
                            onChange={ids => updateMember(m.transaction_id, { people: ids })}
                            onCreatePerson={createPerson}
                          />
                        </td>
                        <td className="px-3 py-2">
                          <TypeSelect member={m} onChange={patch => updateMember(m.transaction_id, patch)} />
                        </td>
                        <td className="px-3 py-2">
                          <RemoveMemberButton member={m} onRemove={() => removeMember(m.transaction_id)} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {/* Phones: one stacked card per member instead of a six-column table. */}
                <ul className="md:hidden divide-y divide-[#1a1d27]">
                  {detail.members.map(m => (
                    <li key={m.transaction_id} className="px-4 py-3 space-y-2 text-xs">
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <p className="text-[#cbd5e1] truncate">{m.raw_description}</p>
                          <p className="text-[#64748b]">{dayjs(m.txn_date).format('DD MMM YY')}</p>
                        </div>
                        <span className={`tabular-nums whitespace-nowrap ${m.debit_credit === 'debit' ? 'text-red-400' : 'text-green-400'}`}>
                          <Amount value={m.amount} debitCredit={m.debit_credit} />
                        </span>
                        <RemoveMemberButton member={m} onRemove={() => removeMember(m.transaction_id)} />
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 min-w-0">
                          <PeoplePicker
                            compact
                            selectedIds={m.people ?? []}
                            allPeople={people}
                            onChange={ids => updateMember(m.transaction_id, { people: ids })}
                            onCreatePerson={createPerson}
                          />
                        </div>
                        <TypeSelect member={m} onChange={patch => updateMember(m.transaction_id, patch)} />
                      </div>
                    </li>
                  ))}
                </ul>
                </>
              ) : (
                <p className="px-4 py-3 text-xs text-[#475569]">No transactions yet. Search below to add some.</p>
              )}

              <AddTransactionSearch existingIds={existingIds} onAdd={addMember} />
              <SettleUp
                settleUp={detail?.settle_up}
                hasPeople={detail?.members?.some(m => m.people?.length) ?? false}
              />
            </>
          )}
        </div>
      )}
    </div>
  )
}
