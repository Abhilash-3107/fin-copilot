import { useEffect, useRef, useState } from 'react'

const FOCUSABLE =
  'a[href], button, input, select, textarea, summary, [tabindex], [contenteditable="true"]'

function isShown(el) {
  for (let node = el; node instanceof HTMLElement; node = node.parentElement) {
    if (getComputedStyle(node).display === 'none') return false
  }
  return getComputedStyle(el).visibility !== 'hidden'
}

// What Tab can reach inside the dialog, in order: enabled, not hidden, not taken
// out of the tab order.
function tabStops(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(
    el =>
      !el.disabled &&
      el.tabIndex >= 0 &&
      !el.closest('[hidden], [inert]') &&
      !(el instanceof HTMLInputElement && el.type === 'hidden') &&
      isShown(el),
  )
}

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
// - Tab and Shift+Tab cycle through the dialog's own controls: focus never
//   leaves it for the page behind, and comes back in from outside.
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
      } else if (e.key === 'Tab') {
        const stops = tabStops(el)
        const first = stops[0]
        const last = stops[stops.length - 1]
        const at = document.activeElement
        let target = null
        if (!first) target = el
        else if (!inside || at === el) target = e.shiftKey ? last : first
        else if (e.shiftKey && at === first) target = last
        else if (!e.shiftKey && at === last) target = first
        if (target) {
          e.preventDefault()
          target.focus()
        }
        e.stopPropagation()
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
