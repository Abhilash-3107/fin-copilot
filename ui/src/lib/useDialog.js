import { useEffect, useRef, useState } from 'react'

// Keyboard and focus for a modal dialog. Call it in the component that is only
// mounted while the dialog is open, and spread the result on the dialog element.
//
// - Focus moves into the dialog when it opens (to the dialog itself, unless a
//   field inside already took it with autoFocus) and returns to what had it before.
// - Escape cancels. Enter confirms, except on a button, link or text area,
//   where it keeps its own meaning.
// - The key never reaches window-level handlers, so a page behind the dialog
//   (the Ledger's Escape, Teach Me's letters) stays out of it.
export function useDialog({ onCancel, onConfirm }) {
  const ref = useRef(null)
  // Read during the first render, before the dialog is in the page.
  const [opener] = useState(() => document.activeElement)

  useEffect(() => {
    const el = ref.current
    if (el && !el.contains(document.activeElement)) el.focus()
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus()
    }
  }, [opener])

  function onKeyDown(e) {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onCancel?.()
    } else if (e.key === 'Enter' && onConfirm) {
      if (e.repeat || e.target.closest?.('button, a, textarea, select')) return
      e.preventDefault()
      e.stopPropagation()
      onConfirm()
    }
  }

  return { ref, tabIndex: -1, onKeyDown }
}
