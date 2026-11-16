import { useId, useMemo, useState } from 'react'
import { categoryOptions, labelOf, matchCategories } from '../lib/categoryOptions.js'

// A filter over every category and subcategory. Typing narrows the list, the
// arrow keys move through it, Enter picks and Escape gives the keyboard back.
// When nothing matches what was typed, it can be created: as a category, or as
// a subcategory of the one being chosen.
export default function CategoryTypeahead({ tree, chosenCategory, onPick, onCreate, onDone, inputRef }) {
  const listId = useId()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [focused, setFocused] = useState(false)
  const [busy, setBusy] = useState(false)

  const options = useMemo(() => categoryOptions(tree), [tree])
  const name = query.trim()

  const choices = useMemo(() => {
    if (!name) return []
    const found = matchCategories(options, name).map(option => ({ kind: 'pick', option, label: labelOf(option) }))
    // Creating is for a name nothing matches: next to real matches it is only clutter.
    if (found.length > 0) return found
    const parent = tree.find(c => !c.parent_id && c.name === chosenCategory)
    return [
      { kind: 'category', name, label: `Create category "${name}"` },
      ...(parent ? [{ kind: 'subcategory', name, parent, label: `Create "${name}" in ${parent.name}` }] : []),
    ]
  }, [name, options, tree, chosenCategory])

  const open = focused && choices.length > 0
  const current = Math.min(active, Math.max(choices.length - 1, 0))

  function finish() {
    setQuery('')
    setActive(0)
    onDone?.()
  }

  async function choose(choice) {
    if (!choice || busy) return
    if (choice.kind === 'pick') {
      onPick(choice.option)
      finish()
      return
    }
    setBusy(true)
    const created = await onCreate(choice.kind === 'subcategory' ? choice.parent.id : null, choice.name)
    setBusy(false)
    if (created) finish()
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (choices.length) setActive((current + (e.key === 'ArrowDown' ? 1 : choices.length - 1)) % choices.length)
    } else if (e.key === 'Enter') {
      // The Enter that confirms an input-method composition is not a pick.
      if (e.nativeEvent?.isComposing || e.isComposing) return
      e.preventDefault()
      choose(choices[current])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      finish()
    }
  }

  return (
    <div className="relative">
      <input
        ref={inputRef}
        role="combobox"
        aria-label="Another category"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open ? `${listId}-${current}` : undefined}
        autoComplete="off"
        value={query}
        disabled={busy}
        placeholder="Another category"
        maxLength={60}
        onChange={e => { setQuery(e.target.value); setActive(0) }}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={onKeyDown}
        className="w-full bg-[#0f1117] border border-[#2d3148] text-[#e2e8f0] pl-3 pr-9 py-2 rounded-lg text-sm focus:outline-none focus:border-[#6366f1] placeholder:text-[#475569]"
      />
      <kbd className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#64748b] bg-[#1e2235] px-1.5 py-0.5 rounded">/</kbd>
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Matching categories"
          className="absolute z-20 left-0 right-0 mt-1 max-h-64 overflow-y-auto rounded-lg border border-[#2d3148] bg-[#13151f] py-1 shadow-xl"
        >
          {choices.map((choice, i) => (
            <li
              key={choice.label}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === current}
              // Keep the input focused through the click, so a pick does not blur it first.
              onMouseDown={e => e.preventDefault()}
              onClick={() => choose(choice)}
              className={`cursor-pointer px-3 py-1.5 text-sm ${
                i === current ? 'bg-[#1e1b4b] text-[#e0e7ff]' : 'text-[#cbd5e1] hover:bg-[#1e2235]'
              } ${choice.kind === 'pick' ? '' : 'text-[#a5b4fc]'}`}
            >
              {choice.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
