import { useEffect, useRef, useState } from 'react'
import { Plus, Trash2, Search, HelpCircle } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import EmptyState from '../components/EmptyState.jsx'
import Tooltip from '../components/Tooltip.jsx'

export default function People() {
  const toast = useToast()
  const [people, setPeople] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [newName, setNewName] = useState('')
  const [newUpi, setNewUpi] = useState('')
  const [creating, setCreating] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editValue, setEditValue] = useState('')
  const editRef = useRef(null)

  async function load(q = '') {
    try {
      const data = await api.get(`/people${q ? `?q=${encodeURIComponent(q)}` : ''}`)
      setPeople(data)
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function create(e) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    try {
      await api.post('/people', { name: newName.trim(), upi: newUpi.trim() || null })
      setNewName('')
      setNewUpi('')
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
      setPeople(ps => ps.map(x => x.id === p.id ? updated : x))
      toast('Saved', 'success')
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error')
    } finally {
      setEditingId(null)
    }
  }

  return (
    <div className="px-6 py-5 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-[#e2e8f0]">People</h1>
        <span className="text-xs text-[#64748b]">{people.length} contacts</span>
      </div>

      {/* Search */}
      <div className="flex items-center gap-2 bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2 focus-within:border-[#6366f1] transition-colors max-w-sm">
        <Search size={14} className="text-[#475569] shrink-0" />
        <input
          value={search}
          onChange={e => handleSearch(e.target.value)}
          placeholder="Search people…"
          className="bg-transparent border-none outline-none text-sm text-[#e2e8f0] flex-1 placeholder:text-[#475569]"
        />
      </div>

      {/* Add person form */}
      <form onSubmit={create} className="flex gap-3 items-center">
        <input
          value={newName}
          onChange={e => setNewName(e.target.value)}
          placeholder="Name"
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] w-44 placeholder:text-[#475569]"
        />
        <input
          value={newUpi}
          onChange={e => setNewUpi(e.target.value)}
          placeholder="Statement name (To help us better match)"
          className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] w-52 placeholder:text-[#475569]"
        />
        <button
          type="submit"
          disabled={creating || !newName.trim()}
          className="flex items-center gap-1.5 bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          <Plus size={14} />
          {creating ? 'Adding…' : 'Add Person'}
        </button>
      </form>

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
                <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">Name</th>
                <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]">
                  <span className="flex items-center gap-1">
                    Statement Match
                    <Tooltip content="Text pattern to auto-match this person in bank statements (e.g., their UPI ID or name as it appears)" position="bottom">
                      <HelpCircle size={11} className="text-[#475569] hover:text-[#94a3b8] transition-colors cursor-help" />
                    </Tooltip>
                  </span>
                </th>
                <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-[#64748b] border-b border-[#1e2235]"></th>
              </tr>
            </thead>
            <tbody>
              {people.map(p => (
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
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => setDeleteTarget(p)}
                      className="text-[#475569] hover:text-red-400 transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

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
