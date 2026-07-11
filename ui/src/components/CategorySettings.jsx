import { useEffect, useState } from 'react'
import { Plus, Pencil, GitMerge, Check, X } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'

// Category management (4.13): add / rename / merge subcategories under each
// top-level category. Top-level categories are fixed; subcategories are the
// grain users actually refine.
export default function CategorySettings() {
  const toast = useToast()
  const [cats, setCats] = useState([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(null)      // parent_id currently adding under
  const [addName, setAddName] = useState('')
  const [editing, setEditing] = useState(null)    // subcategory id being renamed
  const [editName, setEditName] = useState('')
  const [merging, setMerging] = useState(null)    // subcategory id being merged

  async function load() {
    try {
      setCats(await api.get('/categories'))
    } catch (e) {
      toast(`Couldn't load categories - ${e.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const parents = cats.filter(c => c.parent_id === null)
  const subsOf = pid => cats.filter(c => c.parent_id === pid)

  async function addSub(parentId) {
    const name = addName.trim()
    if (!name) return
    try {
      await api.post('/categories/subcategories', { parent_id: parentId, name })
      setAdding(null); setAddName('')
      await load()
      toast(`Added "${name}"`, 'success')
    } catch (e) {
      toast(`Couldn't add - ${e.message}`, 'error')
    }
  }

  async function renameSub(id) {
    const name = editName.trim()
    if (!name) return
    try {
      await api.patch(`/categories/subcategories/${id}`, { name })
      setEditing(null); setEditName('')
      await load()
      toast('Renamed', 'success')
    } catch (e) {
      toast(`Couldn't rename - ${e.message}`, 'error')
    }
  }

  async function mergeSub(sourceId, targetId) {
    try {
      const { moved } = await api.post(`/categories/subcategories/${sourceId}/merge`, { target_id: targetId })
      setMerging(null)
      await load()
      toast(`Merged - ${moved} transaction${moved === 1 ? '' : 's'} moved`, 'info')
    } catch (e) {
      toast(`Couldn't merge - ${e.message}`, 'error')
    }
  }

  if (loading) return <p className="text-sm text-[#475569]">Loading…</p>

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-sm font-semibold text-[#e2e8f0] mb-1">Categories</h2>
        <p className="text-xs text-[#64748b] leading-relaxed max-w-xl">
          Refine the subcategories your copilot sorts transactions into. Rename one and
          the change follows every transaction already using it; merge two to fold their
          transactions together.
        </p>
      </div>

      <div className="space-y-3">
        {parents.map(parent => {
          const subs = subsOf(parent.id)
          return (
            <div key={parent.id} className="bg-[#13151f] border border-[#2d3148] rounded-xl overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#2d3148]">
                <span className="text-sm font-medium text-[#e2e8f0]">{parent.name}</span>
                <button
                  onClick={() => { setAdding(parent.id); setAddName('') }}
                  className="flex items-center gap-1 text-xs text-[#6366f1] hover:text-[#818cf8] transition-colors"
                >
                  <Plus size={13} /> Add
                </button>
              </div>

              <div className="divide-y divide-[#1e2235]">
                {adding === parent.id && (
                  <div className="flex items-center gap-2 px-4 py-2.5">
                    <input
                      autoFocus
                      value={addName}
                      onChange={e => setAddName(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') addSub(parent.id); if (e.key === 'Escape') setAdding(null) }}
                      placeholder="New subcategory"
                      className="flex-1 bg-[#0a0b10] border border-[#6366f1] text-[#e2e8f0] px-2.5 py-1.5 rounded text-sm focus:outline-none"
                    />
                    <button onClick={() => addSub(parent.id)} className="text-[#86efac] hover:opacity-80"><Check size={15} /></button>
                    <button onClick={() => setAdding(null)} className="text-[#64748b] hover:text-[#94a3b8]"><X size={15} /></button>
                  </div>
                )}

                {subs.length === 0 && adding !== parent.id && (
                  <p className="px-4 py-2.5 text-xs text-[#475569]">No subcategories</p>
                )}

                {subs.map(sub => (
                  <div key={sub.id} className="flex items-center gap-2 px-4 py-2 group">
                    {editing === sub.id ? (
                      <>
                        <input
                          autoFocus
                          value={editName}
                          onChange={e => setEditName(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') renameSub(sub.id); if (e.key === 'Escape') setEditing(null) }}
                          className="flex-1 bg-[#0a0b10] border border-[#6366f1] text-[#e2e8f0] px-2.5 py-1.5 rounded text-sm focus:outline-none"
                        />
                        <button onClick={() => renameSub(sub.id)} className="text-[#86efac] hover:opacity-80"><Check size={15} /></button>
                        <button onClick={() => setEditing(null)} className="text-[#64748b] hover:text-[#94a3b8]"><X size={15} /></button>
                      </>
                    ) : merging === sub.id ? (
                      <>
                        <span className="text-sm text-[#94a3b8]">Merge <span className="text-[#e2e8f0]">{sub.name}</span> into</span>
                        <select
                          autoFocus
                          defaultValue=""
                          onChange={e => e.target.value && mergeSub(sub.id, e.target.value)}
                          className="flex-1 bg-[#0a0b10] border border-[#6366f1] text-[#e2e8f0] px-2 py-1.5 rounded text-sm focus:outline-none"
                        >
                          <option value="" disabled>Choose target…</option>
                          {subs.filter(s => s.id !== sub.id).map(s => (
                            <option key={s.id} value={s.id}>{s.name}</option>
                          ))}
                        </select>
                        <button onClick={() => setMerging(null)} className="text-[#64748b] hover:text-[#94a3b8]"><X size={15} /></button>
                      </>
                    ) : (
                      <>
                        <span className="flex-1 text-sm text-[#cbd5e1]">{sub.name}</span>
                        <button
                          onClick={() => { setEditing(sub.id); setEditName(sub.name) }}
                          title="Rename"
                          className="opacity-0 group-hover:opacity-100 text-[#64748b] hover:text-[#a5b4fc] transition-all"
                        >
                          <Pencil size={13} />
                        </button>
                        {subs.length > 1 && (
                          <button
                            onClick={() => setMerging(sub.id)}
                            title="Merge into another subcategory"
                            className="opacity-0 group-hover:opacity-100 text-[#64748b] hover:text-[#a5b4fc] transition-all"
                          >
                            <GitMerge size={13} />
                          </button>
                        )}
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
