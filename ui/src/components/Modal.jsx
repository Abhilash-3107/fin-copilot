import { useEffect } from 'react'
import { X } from 'lucide-react'

// A contained modal: backdrop click and Escape close it, content clicks don't.
// Used for forms that shouldn't live inline on the page (e.g. add-person).
export default function Modal({ open, title, onClose, children, width = 'w-[28rem]' }) {
  useEffect(() => {
    if (!open) return
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 bg-black/60 z-[90] flex items-center justify-center" onClick={onClose}>
      <div
        className={`bg-[#1a1d27] border border-[#2d3148] rounded-xl ${width} max-w-[92vw] p-6`}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-[#e2e8f0]">{title}</h3>
          <button onClick={onClose} className="text-[#64748b] hover:text-[#e2e8f0] transition-colors">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
