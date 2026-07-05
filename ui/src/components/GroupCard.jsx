import { useState } from 'react'
import { ChevronDown, ChevronRight, Trash2, HelpCircle } from 'lucide-react'
import dayjs from 'dayjs'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { formatAmount } from './TransactionTable.jsx'
import Tooltip from './Tooltip.jsx'

const TXN_TYPES = ['split', 'reimbursement', 'refund', 'transfer', 'event']
const ROLES = ['paid', 'received', 'partial']

export default function GroupCard({ group, onDelete }) {
  const toast = useToast()
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)

  async function toggle() {
    if (!expanded && !detail) {
      setLoading(true)
      try {
        const data = await api.get(`/groups/${group.id}`)
        setDetail(data)
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
      const data = await api.get(`/groups/${group.id}`)
      setDetail(data)
    } catch (e) {
      toast(`Update failed: ${e.message}`, 'error')
    }
  }

  async function removeMember(txnId) {
    try {
      await api.delete(`/groups/${group.id}/members/${txnId}`)
      setDetail(d => ({ ...d, members: d.members.filter(m => m.transaction_id !== txnId) }))
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    }
  }

  const labels = Array.isArray(group.labels)
    ? group.labels
    : (group.labels ? group.labels.split(',').filter(Boolean) : [])

  return (
    <div className="bg-[#13151f] border border-[#2d3148] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3">
        <button type="button" onClick={toggle} className="text-[#64748b] hover:text-[#94a3b8] shrink-0">
          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-[#e2e8f0]">{group.name}</span>
            {labels.map(l => (
              <span key={l} className="text-[10px] bg-[#1e2440] text-[#a5b4fc] px-1.5 py-0.5 rounded-full">{l}</span>
            ))}
          </div>
          {group.note && <p className="text-xs text-[#64748b] mt-0.5 truncate">{group.note}</p>}
        </div>
        <button
          type="button"
          onClick={() => onDelete?.(group)}
          className="text-[#475569] hover:text-red-400 transition-colors shrink-0"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* Members */}
      {expanded && (
        <div className="border-t border-[#2d3148]">
          {loading ? (
            <p className="px-4 py-3 text-xs text-[#475569]">Loading…</p>
          ) : detail?.members?.length ? (
            <table className="w-full text-xs">
              <thead>
                <tr>
                  {['Date', 'Description', 'Amount'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">{h}</th>
                  ))}
                  <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">
                    <span className="flex items-center gap-1">
                      Type
                      <Tooltip content="split = shared expense · reimbursement = paid back · refund = returned · transfer = between accounts · event = one-time occasion" position="bottom">
                        <HelpCircle size={11} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
                      </Tooltip>
                    </span>
                  </th>
                  <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">
                    <span className="flex items-center gap-1">
                      Role
                      <Tooltip content="paid = you covered it · received = money came to you · partial = you paid part" position="bottom">
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
                      {formatAmount(m.amount, m.debit_credit)}
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={m.txn_type ?? ''}
                        onChange={e => updateMember(m.transaction_id, { txn_type: e.target.value })}
                        className="bg-[#1a1d27] border border-[#2d3148] text-[#e2e8f0] px-1.5 py-0.5 rounded text-xs focus:outline-none focus:border-[#6366f1]"
                      >
                        <option value="">—</option>
                        {TXN_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={m.role ?? ''}
                        onChange={e => updateMember(m.transaction_id, { role: e.target.value })}
                        className="bg-[#1a1d27] border border-[#2d3148] text-[#e2e8f0] px-1.5 py-0.5 rounded text-xs focus:outline-none focus:border-[#6366f1]"
                      >
                        <option value="">—</option>
                        {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => removeMember(m.transaction_id)}
                        className="text-[#475569] hover:text-red-400 transition-colors"
                      >
                        <Trash2 size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-4 py-3 text-xs text-[#475569]">No members yet.</p>
          )}
        </div>
      )}
    </div>
  )
}
