import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { MoreHorizontal } from 'lucide-react'

const MENU_WIDTH = 176 // matches w-44
const GAP = 4 // between the button and the menu
const MARGIN = 8 // kept clear of the window's edges

// A "more actions" button that opens a short list. The arrow keys move through
// it, Home and End jump to the ends, Enter or Space picks, Escape and Tab close
// it and hand focus back to the button (so a dialog that a choice opens returns
// focus there when it closes). It is drawn in a portal at the button's
// position so a scrolling table around it cannot clip it. It opens below the
// button, flips above it when it would run off the bottom of the window, and
// stays inside the window sideways.
//
// items: [{ label, onSelect, danger? }]
// triggerRef: optional callback that receives the button, for a caller that
// returns focus to it after a flow the menu started (Rename) ends.
export default function ActionMenu({ label, items, triggerRef }) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(null)
  const button = useRef(null)
  const menu = useRef(null)
  const menuId = useId()

  function show() {
    setAt(place(null))
    setOpen(true)
  }

  // Where the menu goes: right-aligned under the button, moved above it or
  // back inside the window when that would not fit. `size` is the drawn menu
  // (unknown on the first pass, when it has not been drawn yet).
  function place(size) {
    const r = button.current.getBoundingClientRect()
    const height = size?.height ?? 0
    let top = r.bottom + GAP
    if (top + height > window.innerHeight - MARGIN) {
      top = r.top - GAP - height >= MARGIN ? r.top - GAP - height : Math.max(MARGIN, window.innerHeight - MARGIN - height)
    }
    const left = Math.min(Math.max(MARGIN, r.right - MENU_WIDTH), window.innerWidth - MENU_WIDTH - MARGIN)
    return { top, left }
  }

  // Once drawn, measure it and move it before it is painted.
  useLayoutEffect(() => {
    if (!open || !menu.current) return
    const next = place(menu.current.getBoundingClientRect())
    setAt(prev => (prev && prev.top === next.top && prev.left === next.left ? prev : next))
  }, [open])

  function close({ refocus = true } = {}) {
    setOpen(false)
    if (refocus) button.current?.focus()
  }

  // Focus the first item when it opens.
  useEffect(() => {
    if (open) menu.current?.querySelector('[role="menuitem"]')?.focus()
  }, [open])

  // Anything that moves the page under the menu closes it, as does a click outside.
  useEffect(() => {
    if (!open) return undefined
    const away = e => {
      if (!menu.current?.contains(e.target) && !button.current?.contains(e.target)) close({ refocus: false })
    }
    const leave = () => close({ refocus: false })
    document.addEventListener('mousedown', away)
    window.addEventListener('resize', leave)
    window.addEventListener('scroll', leave, true)
    return () => {
      document.removeEventListener('mousedown', away)
      window.removeEventListener('resize', leave)
      window.removeEventListener('scroll', leave, true)
    }
  }, [open])

  function onMenuKeyDown(e) {
    const entries = [...menu.current.querySelectorAll('[role="menuitem"]')]
    const index = entries.indexOf(document.activeElement)
    const move = i => { e.preventDefault(); entries[(i + entries.length) % entries.length]?.focus() }
    if (e.key === 'ArrowDown') move(index + 1)
    else if (e.key === 'ArrowUp') move(index - 1)
    else if (e.key === 'Home') move(0)
    else if (e.key === 'End') move(entries.length - 1)
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close() }
    else if (e.key === 'Tab') close()
  }

  return (
    <>
      <button
        ref={node => { button.current = node; triggerRef?.(node) }}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close() : show())}
        className="rounded p-1 text-[#94a3b8] outline-none transition-colors hover:bg-[#1e2235] hover:text-[#e2e8f0] focus-visible:ring-2 focus-visible:ring-[#818cf8]"
      >
        <MoreHorizontal size={16} />
      </button>
      {open && at && createPortal(
        <div
          ref={menu}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKeyDown}
          style={{ top: at.top, left: at.left, width: MENU_WIDTH }}
          className="fixed z-[60] rounded-lg border border-[#2d3148] bg-[#1a1d27] py-1 shadow-xl shadow-black/40"
        >
          {items.map(item => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={() => { close(); item.onSelect() }}
              className={`block w-full px-3 py-2 text-left text-sm outline-none transition-colors hover:bg-[#1e2235] focus:bg-[#1e2235] focus:ring-2 focus:ring-inset focus:ring-[#818cf8] ${
                item.danger ? 'text-[#f87171]' : 'text-[#e2e8f0]'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
