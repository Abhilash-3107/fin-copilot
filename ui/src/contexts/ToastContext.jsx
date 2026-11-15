import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { maskRupees } from '../components/Amount.jsx'
import { usePrivacy } from './PrivacyContext.jsx'

const ToastCtx = createContext(null)

// A toast that must outlive a hard reload (restore, delete-all) is parked in
// sessionStorage and shown by the provider when the reloaded app mounts.
const AFTER_RELOAD_KEY = 'fc_toast_after_reload'

export function reloadWithToast(message, type = 'info', duration = 8000) {
  try {
    sessionStorage.setItem(AFTER_RELOAD_KEY, JSON.stringify({ message, type, duration }))
  } catch {
    // Storage unavailable: the reload still happens, the message is lost.
  }
  window.location.reload()
}

function takeToastAfterReload() {
  try {
    const raw = sessionStorage.getItem(AFTER_RELOAD_KEY)
    if (!raw) return null
    sessionStorage.removeItem(AFTER_RELOAD_KEY)
    const parsed = JSON.parse(raw)
    return typeof parsed?.message === 'string' ? parsed : null
  } catch {
    return null
  }
}

const TYPE_STYLES = {
  success: 'border-green-800 text-green-300',
  error:   'border-red-800 text-red-300',
  info:    'border-[#2d3148] text-[#e2e8f0]',
}

// A message stays as written until it is shown, so hiding amounts also hides
// the ones in a toast that is already on screen. Messages the app builds from
// a figure use useMoney(); this covers text that comes from the server. A
// message that is not a string (a fragment) is shown as written, for a figure
// that must stay visible, such as a policy limit.
function Toast({ message, type, action, onAction }) {
  const { hidden } = usePrivacy()
  return (
    <div
      role={type === 'error' ? 'alert' : 'status'}
      className={`animate-fade-in flex items-center gap-4 bg-[#1e2235] border rounded-lg px-5 py-2.5 text-sm shadow-xl pointer-events-auto ${TYPE_STYLES[type] ?? TYPE_STYLES.info}`}
    >
      <span>{hidden && typeof message === 'string' ? maskRupees(message) : message}</span>
      {action && (
        <button
          onClick={onAction}
          className="shrink-0 font-medium underline underline-offset-2 hover:text-white"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}

const DEFAULT_MS = 2500
// A toast with an action is a chance to take something back: it stays longer.
const ACTION_MS = 8000

// One toast at a time: a new one replaces the one on screen. The third
// argument is a duration in milliseconds, or { duration, action: { label, onClick } }.
export function ToastProvider({ children }) {
  const [current, setCurrent] = useState(null)
  const counter = useRef(0)
  const timer = useRef(null)

  const dismiss = useCallback(() => {
    clearTimeout(timer.current)
    setCurrent(null)
  }, [])

  const toast = useCallback((message, type = 'info', options) => {
    const { duration, action } = typeof options === 'object' && options !== null ? options : { duration: options }
    const id = ++counter.current
    clearTimeout(timer.current)
    setCurrent({ id, message, type, action })
    timer.current = setTimeout(() => setCurrent(c => (c?.id === id ? null : c)), duration ?? (action ? ACTION_MS : DEFAULT_MS))
  }, [])

  useEffect(() => {
    const parked = takeToastAfterReload()
    if (parked) toast(parked.message, parked.type, parked.duration)
    return () => clearTimeout(timer.current)
  }, [toast])

  function runAction() {
    const { action } = current
    dismiss()
    action.onClick()
  }

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-2 items-center pointer-events-none">
        {current && (
          <Toast key={current.id} message={current.message} type={current.type} action={current.action} onAction={runAction} />
        )}
      </div>
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)
