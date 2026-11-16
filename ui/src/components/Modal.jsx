import { useId } from 'react'
import { X } from 'lucide-react'
import { useDialog } from '../lib/useDialog.js'

// A contained modal: backdrop click and Escape close it, content clicks don't.
// Used for forms that shouldn't live inline on the page (e.g. add-person).
export default function Modal({ open, ...props }) {
  return open ? <Panel {...props} /> : null
}

function Panel({ title, onClose, children, width = 'w-[28rem]' }) {
  const titleId = useId()
  const dialog = useDialog({ onCancel: onClose })
  return (
    <div className="fixed inset-0 bg-black/60 z-[90] flex items-center justify-center" onClick={onClose}>
      <div
        {...dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`bg-[#1a1d27] border border-[#2d3148] rounded-xl ${width} max-w-[92vw] p-6 outline-none`}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 id={titleId} className="text-sm font-semibold text-[#e2e8f0]">{title}</h3>
          <button onClick={onClose} aria-label="Close" className="text-[#64748b] hover:text-[#e2e8f0] transition-colors">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
