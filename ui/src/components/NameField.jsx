import { useState } from 'react'
import { Check, X } from 'lucide-react'

// An Enter that is this field's alone. Whatever the Enter does moves focus (the
// dialog hands it back to its opener), and the browser would then press the
// button that has it: so the default action is always cancelled. A held key
// and one that only confirms an input method's composition do nothing.
export function isFieldEnter(e) {
  if (e.key !== 'Enter') return false
  e.preventDefault()
  return !e.repeat && !e.nativeEvent.isComposing
}

// Naming an account, in place: Enter saves, Escape cancels. A blank name goes
// back to the bank and last digits.
export default function NameField({ initial, onSave, onCancel, autoFocus = true }) {
  const [value, setValue] = useState(initial)
  return (
    <div className="flex items-center gap-1.5">
      <input
        autoFocus={autoFocus}
        aria-label="Name this account"
        placeholder="For example, Kotak savings"
        value={value}
        maxLength={60}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => {
          if (isFieldEnter(e)) onSave(value)
          if (e.key === 'Escape') { e.stopPropagation(); onCancel() }
        }}
        className="bg-[#1e2235] border border-[#6366f1] text-[#e2e8f0] px-2 py-0.5 rounded text-sm focus:outline-none w-52 placeholder:text-[#94a3b8]"
      />
      <button type="button" onClick={() => onSave(value)} aria-label="Save name" className="text-[#86efac] hover:opacity-80">
        <Check size={13} />
      </button>
      <button type="button" onClick={onCancel} aria-label="Cancel naming" className="text-[#94a3b8] hover:text-[#e2e8f0]">
        <X size={13} />
      </button>
    </div>
  )
}
