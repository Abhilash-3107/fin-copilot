export default function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', onConfirm, onCancel, danger }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 bg-black/60 z-[90] flex items-center justify-center" onClick={onCancel}>
      <div
        className="bg-[#1a1d27] border border-[#2d3148] rounded-xl w-96 max-w-[90vw] p-6"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-[#e2e8f0] mb-2">{title}</h3>
        {description && <p className="text-sm text-[#94a3b8] mb-5">{description}</p>}
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-md bg-[#1e2235] text-[#94a3b8] text-sm hover:text-[#e2e8f0] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              danger
                ? 'bg-red-700 hover:bg-red-600 text-white'
                : 'bg-[#6366f1] hover:opacity-90 text-white'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
