import { useEffect, useId, useRef, useState } from 'react'

export default function PeoplePicker({ selectedIds = [], allPeople = [], onChange, onCreatePerson, compact = false }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  // Where the arrows moved the highlight; null until they do.
  const [moved, setMoved] = useState(null)
  const ref = useRef(null)
  const listId = useId()

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

  // One ordered list for the mouse and the keyboard: the matches, then "Create".
  const matches = filtered.slice(0, 8)
  const options = [
    ...matches.map(p => ({ kind: 'person', person: p })),
    ...(showCreate ? [{ kind: 'create' }] : []),
  ]
  const menuOpen = open && options.length > 0
  // Typing highlights the first matching person, so Enter picks them. "Create"
  // is only highlighted by the arrows: Enter never makes a new person by accident.
  const active = moved != null
    ? Math.min(moved, options.length - 1)
    : (query.trim() && options[0]?.kind === 'person' ? 0 : -1)
  const optionId = i => `${listId}-${i}`

  function choose(option) {
    if (option.kind === 'person') add(option.person)
    else create()
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!options.length) return
      e.preventDefault()
      setOpen(true)
      const step = e.key === 'ArrowDown' ? 1 : -1
      setMoved(Math.min(Math.max(active + step, 0), options.length - 1))
    } else if (e.key === 'Enter') {
      const option = menuOpen ? options[active] : null
      if (!option) return
      e.preventDefault()
      choose(option)
    } else if (e.key === 'Escape' && menuOpen) {
      // Only closes the menu; the typing stays and nothing behind it reacts.
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
    }
  }

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
          onChange={e => { setQuery(e.target.value); setOpen(true); setMoved(null) }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-expanded={menuOpen}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={menuOpen && active >= 0 ? optionId(active) : undefined}
          placeholder={selected.length === 0 ? (compact ? 'Tag people…' : 'Search people…') : ''}
          aria-label="Search people"
          className={`bg-transparent border-none outline-none text-[#e2e8f0] flex-1 placeholder:text-[#475569] ${compact ? 'text-xs min-w-[60px]' : 'text-sm min-w-[80px]'}`}
        />
      </div>
      {menuOpen && (
        <div
          id={listId}
          role="listbox"
          aria-label="People"
          className="absolute z-20 top-full left-0 right-0 mt-1 bg-[#1a1d27] border border-[#2d3148] rounded-md shadow-xl overflow-hidden"
        >
          {options.map((option, i) => (
            <div
              key={option.kind === 'person' ? option.person.id : 'create'}
              id={optionId(i)}
              role="option"
              aria-selected={i === active}
              onMouseDown={e => e.preventDefault()}
              onMouseEnter={() => setMoved(i)}
              onClick={() => choose(option)}
              className={
                option.kind === 'person'
                  ? `w-full text-left px-3 py-2 text-sm text-[#e2e8f0] cursor-pointer transition-colors ${i === active ? 'bg-[#1e2440]' : 'hover:bg-[#1e2440]'}`
                  : `w-full text-left px-3 py-2 text-sm text-[#6366f1] cursor-pointer transition-colors border-t border-[#2d3148] ${i === active ? 'bg-[#1e2440]' : 'hover:bg-[#1e2440]'}`
              }
            >
              {option.kind === 'person' ? (
                <>
                  {option.person.name}
                  {option.person.upi && <span className="ml-2 text-xs text-[#64748b]">{option.person.upi}</span>}
                </>
              ) : (
                <>+ Create "{query.trim()}"</>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
