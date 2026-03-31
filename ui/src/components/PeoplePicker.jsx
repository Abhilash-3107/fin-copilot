import { useEffect, useRef, useState } from 'react'

export default function PeoplePicker({ selectedIds = [], allPeople = [], onChange, onCreatePerson }) {
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

  const selected = allPeople.filter(p => selectedIds.includes(p.id))
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
      <div className="flex flex-wrap gap-1.5 bg-[#13151f] border border-[#2d3148] rounded-md px-2 py-1.5 min-h-[36px] items-center focus-within:border-[#6366f1] transition-colors">
        {selected.map(p => (
          <span key={p.id} className="flex items-center gap-1 bg-[#1e2440] text-[#a5b4fc] text-xs px-2 py-0.5 rounded-full">
            {p.name}
            <button type="button" onClick={() => remove(p.id)} className="text-[#6366f1] hover:text-white">&times;</button>
          </span>
        ))}
        <input
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          placeholder={selected.length === 0 ? 'Search people…' : ''}
          className="bg-transparent border-none outline-none text-sm text-[#e2e8f0] flex-1 min-w-[80px] placeholder:text-[#475569]"
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
