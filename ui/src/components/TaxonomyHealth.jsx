import { useState } from 'react'
import { GitMerge, ArrowRight, X } from 'lucide-react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

// "Tidy up" for Settings > Categories: subcategories that look like the same
// thing, and transactions carrying a subcategory name that no longer exists.
// Every fix is the user's call; GET /categories/tidy-up only suggests, a merge
// takes a second click, and "Keep both" remembers a deliberate distinction.
export default function TaxonomyHealth({ health, categories, onChanged }) {
  const toast = useToast()
  const [busy, setBusy] = useState(null)
  const [targets, setTargets] = useState({})
  const [confirming, setConfirming] = useState(null)

  if (!health || (health.duplicates.length === 0 && health.orphans.length === 0)) return null

  const pairKey = d => `${d.keep.id}|${d.merge.id}`

  async function merge(d) {
    if (confirming !== pairKey(d)) { setConfirming(pairKey(d)); return }
    setConfirming(null)
    setBusy(pairKey(d))
    try {
      const { moved } = await api.post(`/categories/subcategories/${d.merge.id}/merge`, { target_id: d.keep.id })
      toast(`Merged "${d.merge.name}" into "${d.keep.name}" - ${plural(moved, 'transaction')} moved`, 'info')
      await onChanged()
    } catch (e) {
      toast(`Couldn't merge - ${e.message}`, 'error')
    } finally {
      setBusy(null)
    }
  }

  async function keepBoth(d) {
    setBusy(pairKey(d))
    try {
      await api.post('/categories/tidy-up/dismiss', { keep_id: d.keep.id, merge_id: d.merge.id })
      toast(`Keeping "${d.merge.name}" and "${d.keep.name}" separate`, 'info')
      await onChanged()
    } catch (e) {
      toast(`Couldn't save - ${e.message}`, 'error')
    } finally {
      setBusy(null)
    }
  }

  async function mapOrphan(o) {
    const key = `${o.category_id}|${o.subcategory}`
    const target = targets[key] ?? o.suggestion?.id
    if (!target) return
    setBusy(key)
    try {
      const { moved } = await api.post('/categories/tidy-up/orphans', {
        category_id: o.category_id, name: o.subcategory, target_id: target,
      })
      toast(`Moved ${plural(moved, 'transaction')}`, 'info')
      await onChanged()
    } catch (e) {
      toast(`Couldn't move - ${e.message}`, 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="bg-[#1a1830] border border-[#4f46e5]/30 rounded-xl overflow-hidden">
      <div className="px-4 py-2.5 border-b border-[#4f46e5]/20">
        <span className="text-sm font-medium text-[#e2e8f0]">Tidy up</span>
        <p className="text-xs text-[#94a3b8] mt-0.5">
          Splitting one kind of spending across two labels makes learning and insights weaker.
        </p>
      </div>
      <ul className="divide-y divide-[#2d3148]">
        {health.duplicates.map(d => (
          <li key={pairKey(d)} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 text-sm">
            <span className="text-[#cbd5e1] flex-1 min-w-[14rem]">
              <span className="text-[#64748b]">{d.category} › </span>
              "{d.merge.name}" <span className="text-[#64748b]">({d.merge.uses})</span>
              {d.same_name ? ' is a duplicate of ' : ' looks like '}
              "{d.keep.name}" <span className="text-[#64748b]">({d.keep.uses})</span>
            </span>
            <button
              onClick={() => merge(d)}
              disabled={busy === pairKey(d)}
              className={`flex items-center gap-1.5 text-xs disabled:opacity-40 transition-colors ${
                confirming === pairKey(d) ? 'text-[#fca5a5] hover:text-white' : 'text-[#a5b4fc] hover:text-white'
              }`}
            >
              <GitMerge size={13} />
              {confirming === pairKey(d)
                ? `Move ${plural(d.merge.uses, 'transaction')} and delete "${d.merge.name}"?`
                : `Merge into "${d.keep.name}"`}
            </button>
            <button
              onClick={() => keepBoth(d)}
              disabled={busy === pairKey(d)}
              className="flex items-center gap-1 text-xs text-[#64748b] hover:text-[#cbd5e1] disabled:opacity-40 transition-colors"
            >
              <X size={12} /> Keep both
            </button>
          </li>
        ))}
        {health.orphans.map(o => {
          const key = `${o.category_id}|${o.subcategory}`
          const options = categories.filter(c => c.parent_id === o.category_id)
          const value = targets[key] ?? o.suggestion?.id ?? ''
          return (
            <li key={key} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 text-sm">
              <span className="text-[#cbd5e1] flex-1 min-w-[14rem]">
                {plural(o.count, 'transaction')} in {o.category} use "{o.subcategory}",
                <span className="text-[#64748b]"> which isn't one of its subcategories</span>
              </span>
              <div className="flex items-center gap-2">
                <ArrowRight size={13} className="text-[#64748b]" />
                <select
                  aria-label={`Move "${o.subcategory}" to`}
                  value={value}
                  onChange={e => setTargets(t => ({ ...t, [key]: e.target.value }))}
                  className="bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] text-xs rounded-md px-2 py-1.5 focus:outline-none focus:border-[#6366f1]"
                >
                  <option value="">Choose…</option>
                  {options.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <button
                  onClick={() => mapOrphan(o)}
                  disabled={!value || busy === key}
                  className="text-xs text-[#a5b4fc] hover:text-white disabled:opacity-40 transition-colors"
                >
                  Move
                </button>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
