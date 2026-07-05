import { createContext, useContext, useCallback, useEffect, useState } from 'react'

const PrivacyContext = createContext(null)

const STORAGE_KEY = 'fc_privacy_hidden'

function isEditableTarget(el) {
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

export function PrivacyProvider({ children }) {
  const [hidden, setHidden] = useState(() => localStorage.getItem(STORAGE_KEY) === '1')

  const toggle = useCallback(() => {
    setHidden(prev => {
      const next = !prev
      localStorage.setItem(STORAGE_KEY, next ? '1' : '0')
      return next
    })
  }, [])

  // Global shortcut: Shift+P toggles privacy. Guarded against editable targets
  // so it never eats a keystroke while the user is typing an amount or note.
  useEffect(() => {
    function onKeyDown(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (isEditableTarget(e.target)) return
      if (e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggle])

  return (
    <PrivacyContext.Provider value={{ hidden, toggle }}>
      {children}
    </PrivacyContext.Provider>
  )
}

export function usePrivacy() {
  const ctx = useContext(PrivacyContext)
  if (!ctx) throw new Error('usePrivacy must be used within a PrivacyProvider')
  return ctx
}
