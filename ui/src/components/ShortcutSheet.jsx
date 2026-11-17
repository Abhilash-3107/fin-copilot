import { useEffect, useId, useState } from 'react'
import { X } from 'lucide-react'
import { shortcutScopes, OPEN_SHORTCUTS_EVENT, SHORTCUTS_KEY } from '../lib/shortcuts.js'
import { useDialog } from '../lib/useDialog.js'

function isTyping(el) {
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

// "?" lists every shortcut, by where it works. It never opens while typing, and
// the list comes from lib/shortcuts.js, the same one every shortcut is described in.
export default function ShortcutSheet() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    function onKey(e) {
      if (e.key !== SHORTCUTS_KEY || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return
      e.preventDefault()
      setOpen(true)
    }
    const show = () => setOpen(true)
    window.addEventListener('keydown', onKey)
    window.addEventListener(OPEN_SHORTCUTS_EVENT, show)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener(OPEN_SHORTCUTS_EVENT, show)
    }
  }, [])

  return open ? <Sheet onClose={() => setOpen(false)} /> : null
}

function Sheet({ onClose }) {
  const titleId = useId()
  const dialog = useDialog({ onCancel: onClose })
  return (
    <div className="fixed inset-0 z-[95] flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div
        {...dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-[34rem] max-w-full max-h-[85vh] overflow-y-auto rounded-xl border border-[#2d3148] bg-[#1a1d27] p-6 outline-none"
        onClick={e => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id={titleId} className="text-sm font-semibold text-[#e2e8f0]">Keyboard shortcuts</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-[#94a3b8] transition-colors hover:text-[#e2e8f0]">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-5">
          {shortcutScopes().map(({ scope, items }) => (
            <section key={scope} role="group" aria-label={scope}>
              <h3 className="mb-2 text-xs font-semibold text-[#94a3b8]">{scope}</h3>
              <ul className="divide-y divide-[#2d3148] rounded-lg border border-[#2d3148] bg-[#13151f]">
                {items.map(item => (
                  <li key={item.label} className="flex items-center justify-between gap-4 px-3 py-2">
                    <span className="text-sm text-[#cbd5e1]">{item.label}</span>
                    <span className="flex shrink-0 gap-1">
                      {item.keys.map(k => (
                        <kbd key={k} className="min-w-[1.5rem] rounded bg-[#1e2235] px-1.5 py-0.5 text-center text-xs text-[#e2e8f0]">{k}</kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
