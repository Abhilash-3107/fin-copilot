import { useEffect, useRef, useState } from 'react'

// The open dialogs, oldest first. Only the last one acts on the keyboard.
const stack = []

// Whether any dialog is open, for page code that moves focus on its own.
export const isDialogOpen = () => stack.length > 0

// Keyboard and focus for a modal dialog. Call it in the component that is only
// mounted while the dialog is open, and spread the result on the dialog element.
//
// - Focus moves into the dialog when it opens: to the element marked
//   `data-autofocus`, else to the dialog itself. It returns to what had it before.
// - While the dialog is the topmost one, Escape cancels it wherever focus is,
//   and no other key reaches the page behind it (the Ledger's Escape, Teach
//   Me's letters, the palette's shortcut). A page never acts under a dialog.
// - Enter confirms, except on a button, link, select or text area, where it
//   keeps its own meaning. With `danger` Enter never confirms from the dialog:
//   it only activates the button that has focus (put `data-autofocus` on the
//   safe one, Cancel), so a stray Enter cannot destroy anything.
export function useDialog({ onCancel, onConfirm, danger = false }) {
  const ref = useRef(null)
  // Read during the first render, before the dialog is in the page.
  const [opener] = useState(() => document.activeElement)
  const latest = useRef({})
  latest.current = { onCancel, onConfirm: danger ? undefined : onConfirm }

  useEffect(() => {
    const el = ref.current
    if (el) (el.querySelector('[data-autofocus]') ?? el).focus()

    const entry = {}
    stack.push(entry)

    function onKey(e) {
      if (stack[stack.length - 1] !== entry || e.isComposing) return
      const inside = e.target instanceof Node && el.contains(e.target)
      const { onCancel: cancel, onConfirm: confirm } = latest.current
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        cancel?.()
      } else if (e.key === 'Enter') {
        // On a control inside the dialog, Enter is that control's own.
        if (inside && e.target.closest?.('button, a, textarea, select')) return
        // Behind the dialog it must not activate a page control either.
        if (!inside || confirm) { e.preventDefault(); e.stopPropagation() }
        if (confirm && !e.repeat) confirm()
      } else if (!inside) {
        e.stopPropagation()
      }
    }
    window.addEventListener('keydown', onKey, true)

    return () => {
      window.removeEventListener('keydown', onKey, true)
      stack.splice(stack.indexOf(entry), 1)
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus()
    }
  }, [opener])

  // Keys typed inside the dialog stay inside it, except Cmd/Ctrl combinations
  // (the palette's own toggle).
  function onKeyDown(e) {
    if (!e.metaKey && !e.ctrlKey) e.stopPropagation()
  }

  return { ref, tabIndex: -1, onKeyDown }
}
