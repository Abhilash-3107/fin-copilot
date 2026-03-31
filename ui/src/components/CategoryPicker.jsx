import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'

// Module-level cache — avoids refetching on every panel open within a session
let _cache = null

const SELECT_CLASS =
  'w-full bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-2 rounded-md text-sm focus:outline-none focus:border-[#6366f1] transition-colors'

const LABEL_CLASS =
  'block text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-1.5'

export default function CategoryPicker({ category, subcategory, onChange }) {
  const [tree, setTree] = useState(_cache ?? [])

  useEffect(() => {
    if (_cache) return
    api.get('/categories').then(data => {
      _cache = data
      setTree(data)
    }).catch(() => {})
  }, [])

  const roots = useMemo(() => tree.filter(c => !c.parent_id), [tree])

  const children = useMemo(() => {
    if (!category) return []
    const parent = roots.find(r => r.name === category)
    if (!parent) return []
    return tree.filter(c => c.parent_id === parent.id)
  }, [tree, category, roots])

  function handleCategoryChange(e) {
    onChange({ category: e.target.value, subcategory: '' })
  }

  function handleSubChange(e) {
    onChange({ category, subcategory: e.target.value })
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className={LABEL_CLASS}>
          Category <span className="text-red-400">*</span>
        </label>
        <select value={category} onChange={handleCategoryChange} className={SELECT_CLASS}>
          <option value="">— select category —</option>
          {roots.map(r => (
            <option key={r.id} value={r.name}>{r.name}</option>
          ))}
        </select>
      </div>
      <div>
        <label className={LABEL_CLASS}>Subcategory</label>
        <select
          value={subcategory}
          onChange={handleSubChange}
          className={SELECT_CLASS}
          disabled={!category || children.length === 0}
        >
          <option value="">— none —</option>
          {children.map(c => (
            <option key={c.id} value={c.name}>{c.name}</option>
          ))}
        </select>
      </div>
    </div>
  )
}
