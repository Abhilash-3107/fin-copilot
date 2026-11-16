import { errorReason } from '../lib/errors.js'

// A block that could not load says so, in words, and offers Retry. It stands
// where the data would have been, so a failure never reads as "you have none".
export default function InlineError({ what, error, onRetry, retrying = false, compact = false, className = '' }) {
  const reason = errorReason(error)
  if (compact) {
    // A line of text with a link-style Retry, for a small block beside other controls.
    return (
      <p role="alert" className={`flex items-center gap-2 text-xs text-[#fca5a5] ${className}`}>
        <span>Couldn't load {what}.{reason && ` ${reason}`}</span>
        {onRetry && (
          <button type="button" onClick={onRetry} disabled={retrying} className="underline underline-offset-2 hover:text-white disabled:opacity-50">
            {retrying ? 'Retrying…' : 'Retry'}
          </button>
        )}
      </p>
    )
  }
  return (
    <div
      role="alert"
      className={`flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-[#7f1d1d] bg-[#2a1215] px-4 py-3 ${className}`}
    >
      <p className="flex-1 min-w-[12rem] text-sm text-[#fecaca]">
        Couldn't load {what}.{reason && <span className="text-[#fca5a5]"> {reason}</span>}
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="shrink-0 rounded-md border border-[#7f1d1d] px-3 py-1.5 text-xs font-medium text-[#fecaca] transition-colors hover:bg-[#3b1a1e] disabled:opacity-50"
        >
          {retrying ? 'Retrying…' : 'Retry'}
        </button>
      )}
    </div>
  )
}
