import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Trash2, Search, HelpCircle } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import { txnFilterPath } from '../lib/txnLink.js'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import EmptyState from '../components/EmptyState.jsx'
import Modal from '../components/Modal.jsx'
import Tooltip from '../components/Tooltip.jsx'
import Amount from '../components/Amount.jsx'

// Curated relationship choices. Family ones (dad/mom/…) make this person's
// transfers land in Transfers › Family instead of the generic Peer Transfer;
// "self" (the user's own other accounts) makes them Self Transfers. The
// mapping lives server-side (people.relationship_category).
const RELATIONSHIPS = [
  'self', 'dad', 'mom', 'sister', 'brother', 'wife', 'husband',
  'son', 'daughter', 'family', 'friend', 'roommate', 'colleague', 'other',
]
const RELATIONSHIP_LABELS = { self: 'Me (my other account)' }

export default function People() {
  const toast = useToast()
  const navigate = useNavigate()
  const [people, setPeople] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [newName, setNewName] = useState('')
  const [newUpi, setNewUpi] = useState('')
  const [newRel, setNewRel] = useState('')
  const [creating, setCreating] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editValue, setEditValue] = useState('')
  const editRef = useRef(null)

  const load = useCallback(async (q = '') => {
    try {
      const data = await api.get(`/people${q ? `?q=${encodeURIComponent(q)}` : ''}`)
      setPeople(data)
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { load() }, [load])

  async function create(e) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    try {
      await api.post('/people', { name: newName.trim(), upi: newUpi.trim() || null, relationship: newRel || null })
      setNewName('')
      setNewUpi('')
      setNewRel('')
      setAddOpen(false)
      load(search)
      toast('Person added', 'success')
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    } finally {
      setCreating(false)
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return
    try {
      await api.delete(`/people/${deleteTarget.id}`)
      setPeople(ps => ps.filter(p => p.id !== deleteTarget.id))
      toast(`Deleted "${deleteTarget.name}"`, 'info')
      setDeleteTarget(null)
    } catch (e) {
      toast(`Delete failed: ${e.message}`, 'error')
    }
  }

  function handleSearch(q) {
    setSearch(q)
    load(q)
  }

  function startEdit(p) {
    setEditingId(p.id)
    setEditValue(p.upi ?? '')
    setTimeout(() => editRef.current?.focus(), 0)
  }

  async function commitEdit(p) {
    const val = editValue.trim() || null
    if (val === (p.upi ?? null)) { setEditingId(null); return }
    try {
      const updated = await api.patch(`/people/${p.id}`, { name: p.name, upi: val })
      setPeople(ps => ps.map(x => x.id === p.id ? { ...updated, stats: x.stats } : x))
      toast('Saved', 'success')
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    } finally {
      setEditingId(null)
    }
  }

  async function commitRelationship(p, relationship) {
    const val = relationship || null
    if (val === (p.relationship ?? null)) return
    try {
      const updated = await api.patch(`/people/${p.id}`, { name: p.name, upi: p.upi ?? null, relationship: val })
      setPeople(ps => ps.map(x => x.id === p.id ? { ...updated, stats: x.stats } : x))
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    }
  }

  // Click-through: filter the transaction list to this person's matched rows.
  // Carries the display name in navigation state so the chip reads as a name.
  function viewTransactions(p) {
    if (!p.upi) {
      toast('Add a statement match first so we can find their transactions', 'info')
      return
    }
    navigate(txnFilterPath({ person: p.upi }), { state: { personName: p.name } })
  }

  const th = 'px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]'

  return (
    <div className="px-6 py-5 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-[#e2e8f0]">People</h1>
        <span className="text-xs text-[#64748b]">{people.length} contacts</span>
      </div>

      {/* Search + add trigger */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2 focus-within:border-[#6366f1] transition-colors max-w-sm flex-1">
          <Search size={14} className="text-[#475569] shrink-0" />
          <input
            value={search}
            onChange={e => handleSearch(e.target.value)}
            placeholder="Search people…"
            className="bg-transparent border-none outline-none text-sm text-[#e2e8f0] flex-1 placeholder:text-[#475569]"
          />
        </div>
        <button
          onClick={() => setAddOpen(true)}
          className="flex items-center gap-1.5 bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 transition-opacity"
        >
          <Plus size={14} />
          Add Person
        </button>
      </div>

      {/* Table */}
      {loading ? (
        <p className="text-sm text-[#475569]">Loading…</p>
      ) : people.length === 0 ? (
        <EmptyState
          title="No people yet"
          description="Add people to track who is involved in group transactions."
        />
      ) : (
        <div className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-visible">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={th}>Name</th>
                <th className={th}>
                  <span className="flex items-center gap-1">
                    Statement Match
                    <Tooltip content="Text pattern to auto-match this person in bank statements (e.g., their UPI ID or name as it appears)" position="bottom">
                      <HelpCircle size={11} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
                    </Tooltip>
                  </span>
                </th>
                <th className={th}>
                  <span className="flex items-center gap-1">
                    Relationship
                    <Tooltip content="Family relationships (dad, mom, sister…) file this person's transfers under Transfers › Family. Me (my other account) files them as Self Transfers. Others are Peer Transfers." position="bottom">
                      <HelpCircle size={11} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
                    </Tooltip>
                  </span>
                </th>
                <th className={`${th} text-right`}>Matched</th>
                <th className={`${th} text-right`}>
                  <span className="inline-flex items-center gap-1">
                    Net
                    <Tooltip content="Received from them minus sent to them, across matched transactions. Green = they sent you more; red = you sent more." position="bottom">
                      <HelpCircle size={11} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
                    </Tooltip>
                  </span>
                </th>
                <th className={`${th} w-10`}></th>
              </tr>
            </thead>
            <tbody>
              {people.map(p => {
                const stats = p.stats ?? { matched: 0, net: 0, sent: 0, received: 0 }
                const clickable = stats.matched > 0
                return (
                  <tr key={p.id} className="border-b border-[#1a1d27] hover:bg-[#1a1d27] transition-colors">
                    <td className="px-4 py-3 text-[#e2e8f0] font-medium">{p.name}</td>
                    <td className="px-4 py-3 text-[#94a3b8]" onClick={() => startEdit(p)}>
                      {editingId === p.id ? (
                        <input
                          ref={editRef}
                          value={editValue}
                          onChange={e => setEditValue(e.target.value)}
                          onBlur={() => commitEdit(p)}
                          onKeyDown={e => { if (e.key === 'Enter') commitEdit(p); if (e.key === 'Escape') setEditingId(null) }}
                          className="bg-[#1e2235] border border-[#6366f1] text-[#e2e8f0] px-2 py-0.5 rounded text-sm outline-none w-full"
                          placeholder="To help us better match"
                          onClick={e => e.stopPropagation()}
                        />
                      ) : (
                        <span className={`cursor-pointer hover:text-[#e2e8f0] transition-colors ${!p.upi ? 'text-[#475569]' : ''}`}>
                          {p.upi ?? '— click to set'}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={p.relationship ?? ''}
                        onChange={e => commitRelationship(p, e.target.value)}
                        className={`bg-transparent border border-transparent hover:border-[#2d3148] focus:border-[#6366f1] px-2 py-1 rounded text-sm outline-none capitalize ${p.relationship ? 'text-[#94a3b8]' : 'text-[#475569]'}`}
                      >
                        <option value="">— none</option>
                        {RELATIONSHIPS.map(r => (
                          <option key={r} value={r} className="text-[#e2e8f0] capitalize">{RELATIONSHIP_LABELS[r] ?? r}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {clickable ? (
                        <button
                          onClick={() => viewTransactions(p)}
                          className="text-[#a5b4fc] hover:underline"
                          title={`View ${stats.matched} transaction${stats.matched === 1 ? '' : 's'} for ${p.name}`}
                        >
                          {stats.matched}
                        </button>
                      ) : (
                        <span className="text-[#475569]">0</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {stats.matched > 0 ? (
                        <span className={stats.net > 0 ? 'text-green-400' : stats.net < 0 ? 'text-red-400' : 'text-[#94a3b8]'}>
                          {stats.net > 0 ? '+' : ''}<Amount value={stats.net} decimals={0} />
                        </span>
                      ) : (
                        <span className="text-[#475569]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => setDeleteTarget(p)}
                        aria-label={`Delete ${p.name}`}
                        className="inline-flex items-center align-middle text-[#475569] hover:text-red-400 transition-colors"
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add person modal */}
      <Modal open={addOpen} title="Add a person" onClose={() => setAddOpen(false)}>
        <form onSubmit={create} className="space-y-3">
          <div>
            <label className="block text-xs text-[#94a3b8] mb-1">Name</label>
            <input
              autoFocus
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="e.g. Karabi"
              className="w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569]"
            />
          </div>
          <div>
            <label className="block text-xs text-[#94a3b8] mb-1">Statement match <span className="text-[#475569]">(to help us match their transactions)</span></label>
            <input
              value={newUpi}
              onChange={e => setNewUpi(e.target.value)}
              placeholder="UPI id or name as it appears"
              className="w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569]"
            />
          </div>
          <div>
            <label className="block text-xs text-[#94a3b8] mb-1">Relationship</label>
            <select
              value={newRel}
              onChange={e => setNewRel(e.target.value)}
              className={`w-full bg-[#13151f] border border-[#2d3148] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] ${newRel ? 'text-[#e2e8f0]' : 'text-[#475569]'}`}
            >
              <option value="">Relationship…</option>
              {RELATIONSHIPS.map(r => (
                <option key={r} value={r} className="text-[#e2e8f0] capitalize">{RELATIONSHIP_LABELS[r] ?? r}</option>
              ))}
            </select>
          </div>
          <div className="flex gap-3 justify-end pt-2">
            <button
              type="button"
              onClick={() => setAddOpen(false)}
              className="px-4 py-2 rounded-md bg-[#1e2235] text-[#94a3b8] text-sm hover:text-[#e2e8f0] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={creating || !newName.trim()}
              className="flex items-center gap-1.5 bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              <Plus size={14} />
              {creating ? 'Adding…' : 'Add Person'}
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete person?"
        description={`Remove "${deleteTarget?.name}" from your contacts? This does not remove them from existing group memberships.`}
        confirmLabel="Delete"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  )
}
