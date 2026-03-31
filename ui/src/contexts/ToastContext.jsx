import { createContext, useCallback, useContext, useRef, useState } from 'react'

const ToastCtx = createContext(null)

const TYPE_STYLES = {
  success: 'border-green-800 text-green-300',
  error:   'border-red-800 text-red-300',
  info:    'border-[#2d3148] text-[#e2e8f0]',
}

function Toast({ message, type }) {
  return (
    <div
      className={`animate-fade-in bg-[#1e2235] border rounded-lg px-5 py-2.5 text-sm shadow-xl pointer-events-auto ${TYPE_STYLES[type] ?? TYPE_STYLES.info}`}
    >
      {message}
    </div>
  )
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const counter = useRef(0)

  const toast = useCallback((message, type = 'info', duration = 2500) => {
    const id = ++counter.current
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), duration)
  }, [])

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-2 items-center pointer-events-none">
        {toasts.map(t => (
          <Toast key={t.id} message={t.message} type={t.type} />
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)
