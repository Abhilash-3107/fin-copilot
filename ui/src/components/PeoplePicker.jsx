import { useEffect, useRef, useState } from 'react'

export default function PeoplePicker({ selectedIds = [], allPeople = [], onChange, onCreatePerson, compact = false }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Keep tags that don't match a known person (free-text names from before the
  // people table) visible, so editing a row never silently drops them.
  const byId = new Map(allPeople.map(p => [p.id, p]))
  const selected = selectedIds.map(id => byId.get(id) ?? { id, name: id })
  const filtered = allPeople.filter(
    p => !selectedIds.includes(p.id) && p.name.toLowerCase().includes(query.toLowerCase())
  )
  const showCreate = query.trim() && !allPeople.some(p => p.name.toLowerCase() === query.toLowerCase())

  function add(person) {
    onChange([...selectedIds, person.id])
    setQuery('')
    setOpen(false)
  }

  function remove(id) {
    onChange(selectedIds.filter(x => x !== id))
  }

  async function create() {
    if (!onCreatePerson) return
    const person = await onCreatePerson(query.trim())
    if (person) {
      onChange([...selectedIds, person.id])
      setQuery('')
      setOpen(false)
    }
  }

  return (
    <div ref={ref} className="relative">
      <div className={`flex flex-wrap items-center bg-[#13151f] border border-[#2d3148] rounded-md focus-within:border-[#6366f1] transition-colors ${compact ? 'gap-1 px-1.5 py-0.5 min-h-[26px]' : 'gap-1.5 px-2 py-1.5 min-h-[36px]'}`}>
        {selected.map(p => (
          <span key={p.id} className={`flex items-center gap-1 bg-[#1e2440] text-[#a5b4fc] rounded-full ${compact ? 'text-[11px] px-1.5 py-0' : 'text-xs px-2 py-0.5'}`}>
            {p.name}
            <button type="button" onClick={() => remove(p.id)} aria-label={`Remove ${p.name}`} className="text-[#6366f1] hover:text-white">&times;</button>
          </span>
        ))}
        <input
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          placeholder={selected.length === 0 ? (compact ? 'Tag people…' : 'Search people…') : ''}
          aria-label="Search people"
          className={`bg-transparent border-none outline-none text-[#e2e8f0] flex-1 placeholder:text-[#475569] ${compact ? 'text-xs min-w-[60px]' : 'text-sm min-w-[80px]'}`}
        />
      </div>
      {open && (filtered.length > 0 || showCreate) && (
        <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-[#1a1d27] border border-[#2d3148] rounded-md shadow-xl overflow-hidden">
          {filtered.slice(0, 8).map(p => (
            <button
              key={p.id}
              type="button"
              onClick={() => add(p)}
              className="w-full text-left px-3 py-2 text-sm text-[#e2e8f0] hover:bg-[#1e2440] transition-colors"
            >
              {p.name}
              {p.upi && <span className="ml-2 text-xs text-[#64748b]">@{p.upi}</span>}
            </button>
          ))}
          {showCreate && (
            <button
              type="button"
              onClick={create}
              className="w-full text-left px-3 py-2 text-sm text-[#6366f1] hover:bg-[#1e2440] transition-colors border-t border-[#2d3148]"
            >
              + Create "{query.trim()}"
            </button>
          )}
        </div>
      )}
    </div>
  )
}
