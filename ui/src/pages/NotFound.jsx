import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-24 text-center">
      <h1 className="text-lg font-semibold text-[#e2e8f0]">Page not found</h1>
      <p className="max-w-sm text-sm text-[#94a3b8]">There is nothing at this address. It may have moved, or the link is mistyped.</p>
      <Link
        to="/dashboard"
        className="mt-2 rounded-lg bg-[#6366f1] px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
      >
        Back to the Dashboard
      </Link>
    </div>
  )
}
