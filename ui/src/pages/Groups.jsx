import { useCallback, useState } from 'react'
import { Plus, Search } from 'lucide-react'
import { api } from '../lib/api.js'
import { count } from '../lib/plural.js'
import { useToast } from '../contexts/ToastContext.jsx'
import GroupCard from '../components/GroupCard.jsx'
import EmptyState from '../components/EmptyState.jsx'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import InlineError from '../components/InlineError.jsx'
import { useLoad } from '../lib/useLoad.js'

export default function Groups() {
  const toast = useToast()
  const [search, setSearch] = useState('')
  const [newName, setNewName] = useState('')
  const [createError, setCreateError] = useState('')
  const [creating, setCreating] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)

  // The list follows the search box; an answer to an older keystroke is dropped.
  const { data, error, reload, setData } = useLoad(useCallback(
    () => api.get(`/groups${search ? `?q=${encodeURIComponent(search)}` : ''}`), [search],
  ))
  const groups = data ?? []

  async function createGroup(e) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    setCreateError('')
    try {
      await api.post('/groups', { name: newName.trim() })
      setNewName('')
      reload()
      toast('Group created', 'success')
    } catch (e) {
      // Beside the field that caused it, e.g. a name that already exists.
      setCreateError(e.message)
    } finally {
      setCreating(false)
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return
    try {
      await api.delete(`/groups/${deleteTarget.id}`)
      setData(gs => (gs ?? []).filter(g => g.id !== deleteTarget.id))
      toast(`Deleted "${deleteTarget.name}"`, 'info')
      setDeleteTarget(null)
    } catch (e) {
      toast(`Delete failed: ${e.message}`, 'error')
    }
  }

  return (
    <div className="px-6 py-5 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-[#e2e8f0]">Groups</h1>
        <span className="text-xs text-[#64748b]">{count(groups.length, 'group')}</span>
      </div>

      {/* Search + create */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="flex items-center gap-2 flex-1 bg-[#13151f] border border-[#2d3148] rounded-md px-3 py-2 focus-within:border-[#6366f1] transition-colors">
          <Search size={14} className="text-[#475569] shrink-0" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search groups…"
            className="bg-transparent border-none outline-none text-sm text-[#e2e8f0] flex-1 placeholder:text-[#475569]"
          />
        </div>
        <form onSubmit={createGroup} className="flex gap-2">
          <input
            value={newName}
            aria-label="New group name"
            onChange={e => { setNewName(e.target.value); setCreateError('') }}
            placeholder="New group name…"
            className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-3 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] flex-1 sm:flex-none sm:w-52 min-w-0 placeholder:text-[#475569]"
          />
          <button
            type="submit"
            disabled={creating || !newName.trim()}
            className="flex items-center gap-1.5 bg-[#6366f1] text-white px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <Plus size={14} />
            {creating ? 'Creating…' : 'Create'}
          </button>
        </form>
      </div>

      {createError && <p role="alert" className="text-sm text-[#fca5a5]">{createError}</p>}

      {/* Group list */}
      {error ? (
        <InlineError what="your groups" error={error} onRetry={reload} />
      ) : data == null ? (
        <p className="text-sm text-[#475569]">Loading…</p>
      ) : groups.length === 0 ? (
        <EmptyState
          title="No groups yet"
          description="Create a group to organize related transactions: trips, shared expenses, recurring splits."
        />
      ) : (
        <div className="space-y-3">
          {groups.map(g => (
            <GroupCard
              key={g.id}
              group={g}
              onDelete={setDeleteTarget}
              onUpdate={updated => setData(gs => (gs ?? []).map(x => (x.id === updated.id ? { ...x, ...updated } : x)))}
            />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete group?"
        description={`This will delete "${deleteTarget?.name}" and remove all its member associations.`}
        confirmLabel="Delete"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  )
}
