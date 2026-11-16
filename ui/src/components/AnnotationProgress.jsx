import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { Sparkles, ArrowRight } from 'lucide-react'

// What each background job kind is doing, in the user's words, and where the
// card leads. A re-embed (model switch, or an embed-format upgrade on launch)
// rebuilds retrieval memory; it sorts nothing, so it must not say it does. A
// model download blocks sorting on this computer, so it shows here too: its
// numbers are bytes. A kind this list has not met still gets a plain card.
const KINDS = {
  annotate: { title: 'Sorting your payments', to: '/transactions' },
  reembed: { title: 'Refreshing AI memory', to: '/settings' },
  model_download: { title: 'Downloading the AI model', to: '/settings?tab=ai', bytes: true },
}
const UNKNOWN_KIND = { title: 'Working in the background', to: '/settings' }

function gigabytesOrMegabytes(n) {
  return n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`
}

// Floating realtime progress card for a running background job.
// `job` is the polled annotation_jobs row ({status, processed, total, kind}) or
// null. Renders nothing when there is no active job.
export default function AnnotationProgress({ job }) {
  if (!job) return null
  const { title, to, bytes } = KINDS[job.kind] ?? UNKNOWN_KIND

  const total = job.total ?? 0
  const processed = job.processed ?? 0
  // total is 0 until the pipeline counts the batch - show an indeterminate bar.
  const indeterminate = total === 0 || job.status === 'queued'
  const pct = indeterminate ? 0 : Math.min(100, Math.round((processed / total) * 100))

  return createPortal(
    <Link
      to={to}
      className="group fixed bottom-5 right-5 z-50 block w-80 bg-[#13151f] border border-[#2d3148] rounded-xl shadow-2xl shadow-black/40 px-4 py-3.5 transition-colors hover:border-[#7c3aed]/70"
    >
      <div className="flex items-center gap-2 mb-2.5">
        <Sparkles size={15} className="text-[#a78bfa] shrink-0" />
        <span className="text-sm font-medium text-[#e2e8f0]">{title}</span>
        <ArrowRight
          size={14}
          className="ml-auto shrink-0 text-[#94a3b8] opacity-0 transition-opacity group-hover:opacity-100"
        />
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
        <span>
          {indeterminate
            ? 'Getting started…'
            : bytes
              ? `${gigabytesOrMegabytes(processed)} of ${gigabytesOrMegabytes(total)}`
              : `${processed} of ${total}`}
        </span>
        {!indeterminate && <span>{pct}%</span>}
      </div>
    </Link>,
    document.body,
  )
}
