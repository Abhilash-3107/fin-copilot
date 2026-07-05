import { createPortal } from 'react-dom'
import { Sparkles } from 'lucide-react'

// Floating realtime progress card for a running auto-annotate job.
// `job` is the polled annotation_jobs row ({status, processed, total}) or null.
// Renders nothing when there is no active job.
export default function AnnotationProgress({ job }) {
  if (!job) return null

  const total = job.total ?? 0
  const processed = job.processed ?? 0
  // total is 0 until the pipeline counts the batch — show an indeterminate bar.
  const indeterminate = total === 0 || job.status === 'queued'
  const pct = indeterminate ? 0 : Math.min(100, Math.round((processed / total) * 100))

  return createPortal(
    <div className="fixed bottom-5 right-5 z-50 w-80 bg-[#13151f] border border-[#2d3148] rounded-xl shadow-2xl shadow-black/40 px-4 py-3.5">
      <div className="flex items-center gap-2 mb-2.5">
        <Sparkles size={15} className="text-[#a78bfa] shrink-0" />
        <span className="text-sm font-medium text-[#e2e8f0]">Categorizing your transactions</span>
      </div>

      <div className="h-1.5 bg-[#1e2235] rounded-full overflow-hidden">
        {indeterminate ? (
          <div className="h-full w-1/3 bg-[#7c3aed] rounded-full animate-progress-indeterminate" />
        ) : (
          <div
            className="h-full bg-[#7c3aed] rounded-full transition-[width] duration-500 ease-out"
            style={{ width: `${pct}%` }}
          />
        )}
      </div>

      <div className="flex justify-between mt-2 text-xs text-[#94a3b8] tabular-nums">
        <span>{indeterminate ? 'Getting started…' : `${processed} of ${total}`}</span>
        {!indeterminate && <span>{pct}%</span>}
      </div>
    </div>,
    document.body,
  )
}
